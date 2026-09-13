#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查官方模拟器导出的 ``.jlog`` / ``.psum`` 包。

背景
----
官方导出的行为日志不是裸 JSON，而是一个自包含信封：

    JMBPLOG1
    uint16 envelope_version
    uint16 reserved
    uint16 header_json_length
    header_json   # packagefmt.EnvelopeHeader
    frame*        # 每帧: uint32 seq + uint32 plaintext_len + uint32 cipher_len + ciphertext
    signature     # 64 字节 Ed25519 签名（包体完整性，官方服务端验证）

正文是 gzip 后再做 AES-256-GCM 分块加密的。DEK 被 RSA-OAEP-SHA256 包装给
``wrapped_deks`` 里的若干个**服务端公钥**；本地模拟器只有公钥，因此如果没有
官方 wrap 私钥，不能解出正文。这个脚本做两件事：

1. 不依赖私钥，完整解析信封 metadata、帧长度、压缩后长度、签名长度；
2. 如果用户拥有对应 wrap 私钥（``--private-key``），尝试解包 DEK、逐帧 AES-GCM
   解密并 gzip 解压，把 behavior payload 写成 JSONL。

注意：私钥只应是你自己合法持有的测试/复盘密钥。官方服务端私钥不可得时，脚本只
能给出 metadata。
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


MAGIC_JLOG = b"JMBPLOG1"
MAGIC_PSUM = b"JMBPSUM1"


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


@dataclass(slots=True)
class Envelope:
    path: Path
    magic: bytes
    envelope_version: int
    reserved: int
    header_json: dict[str, Any]
    header_bytes: bytes
    frames: list[dict[str, Any]]
    signature: bytes
    trailing_len: int


def parse_envelope(path: Path) -> Envelope:
    raw = path.read_bytes()
    if len(raw) < 14:
        raise ValueError(f"{path}: 文件太短")
    if raw[:8] not in (MAGIC_JLOG, MAGIC_PSUM):
        raise ValueError(f"{path}: 未知 magic {raw[:8]!r}")

    version, reserved, header_len = struct.unpack(">HHH", raw[8:14])
    if 14 + header_len > len(raw):
        raise ValueError(f"{path}: header_json_length={header_len} 超出文件")
    header_bytes = raw[14 : 14 + header_len]
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:  # pragma: no cover - 仅用于诊断
        raise ValueError(f"{path}: header JSON 不是 UTF-8") from exc

    body = raw[14 + header_len :]
    offset = 0
    frames: list[dict[str, Any]] = []
    # 帧格式可由本地官方 exe 的 WriteEnvelope/encryptedFrameWriter 反汇编确认：
    # seq/plaintext_len/cipher_len 均为 big-endian uint32；cipher_len 含 16B GCM tag。
    while offset + 12 <= len(body):
        seq, plaintext_len, cipher_len = struct.unpack(">III", body[offset : offset + 12])
        end = offset + 12 + cipher_len
        if end > len(body):
            break
        frames.append(
            {
                "seq": seq,
                "plaintext_len": plaintext_len,
                "cipher_len": cipher_len,
                "frame_offset": offset,
                "cipher_offset": offset + 12,
            }
        )
        offset = end

    trailing = body[offset:]
    # 最后一帧之后是 64 字节签名；正常包 trailing_len == 64。
    signature = trailing[:64]
    return Envelope(
        path=path,
        magic=raw[:8],
        envelope_version=version,
        reserved=reserved,
        header_json=header,
        header_bytes=header_bytes,
        frames=frames,
        signature=signature,
        trailing_len=len(trailing),
    )


def _rsa_unwrap_dek(private_key_path: Path, wrapped_b64: str) -> bytes:
    """RSA-OAEP-SHA256 解包 DEK。需要 cryptography。"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("解密需要 pip install cryptography") from exc

    key_data = private_key_path.read_bytes()
    private_key = serialization.load_pem_private_key(key_data, password=None)
    wrapped = b64url_decode(wrapped_b64)
    return private_key.decrypt(
        wrapped,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def _nonce_candidates(prefix: bytes, seq: int) -> Iterator[bytes]:
    """AES-GCM nonce 候选。具体由包格式决定，逐个试 tag 最稳。"""
    if len(prefix) == 12:
        yield prefix
        return
    # 已观察到的 nonce_prefix_b64 为 8 字节；Go 里常见 suffix counter。
    if len(prefix) == 8:
        for order in ("big", "little"):
            yield prefix + seq.to_bytes(4, order)
            yield seq.to_bytes(4, order) + prefix
    # 最后兜底：前缀 + 4 字节 counter，无论前缀多长。
    for order in ("big", "little"):
        counter = seq.to_bytes(4, order)
        if len(prefix) + 4 == 12:
            yield prefix + counter
            yield counter + prefix


def _aad_candidates(frame_header: bytes, seq: int, plaintext_len: int, cipher_len: int) -> Iterator[bytes]:
    yield b""
    yield frame_header
    yield struct.pack(">I", seq)
    yield struct.pack(">II", plaintext_len, cipher_len)
    yield struct.pack(">III", seq, plaintext_len, cipher_len)


def _try_decrypt_frames(
    raw: bytes,
    env: Envelope,
    dek: bytes,
) -> tuple[bytes, str]:
    """返回 (plaintext_concat, 命中的 nonce/aad 描述)。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("解密需要 pip install cryptography") from exc

    if len(dek) not in (16, 24, 32):
        raise ValueError(f"DEK 长度异常：{len(dek)}")

    header_len = struct.unpack(">H", raw[12:14])[0]
    body = raw[14 + header_len :]
    aes = AESGCM(dek)
    prefix = b64url_decode(env.header_json["nonce_prefix_b64"])
    out = bytearray()
    for frame in env.frames:
        start = frame["cipher_offset"]
        end = start + frame["cipher_len"]
        ciphertext = body[start:end]
        header = body[frame["frame_offset"] : frame["frame_offset"] + 12]
        errors: list[str] = []
        hit: tuple[bytes, bytes, str] | None = None
        for nonce in _nonce_candidates(prefix, frame["seq"]):
            for aad in _aad_candidates(header, frame["seq"], frame["plaintext_len"], frame["cipher_len"]):
                try:
                    plain = aes.decrypt(nonce, ciphertext, aad)
                except Exception as exc:  # noqa: BLE001 - GCM 校验失败是预期分支
                    errors.append(str(exc))
                    continue
                hit = (plain, nonce, aad)
                break
            if hit is not None:
                break
        if hit is None:
            raise ValueError(
                "AES-GCM 解密失败：没有 nonce/AAD 候选通过认证。"
                "可能私钥不对，或包格式版本不同。"
            )
        plain, nonce, aad = hit
        out.extend(plain)
    return bytes(out), "ok"


def decrypt_envelope(path: Path, private_key: Path, key_id: str | None = None) -> bytes:
    env = parse_envelope(path)
    wrapped = env.header_json.get("wrapped_deks") or []
    candidates = [w for w in wrapped if key_id is None or w.get("key_id") == key_id]
    if not candidates:
        raise ValueError(f"{path}: 没有匹配的 wrapped_deks（key_id={key_id!r}）")
    raw = path.read_bytes()
    last_error: Exception | None = None
    for item in candidates:
        try:
            dek = _rsa_unwrap_dek(private_key, item["wrapped_dek_b64"])
            plain, note = _try_decrypt_frames(raw, env, dek)
            return plain
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"{path}: 所有 wrapped_dek 都失败：{last_error}")


def envelope_summary(env: Envelope) -> dict[str, Any]:
    h = env.header_json
    return {
        "path": str(env.path),
        "magic": env.magic.decode("ascii", "replace"),
        "package_type": h.get("package_type"),
        "problem_no": h.get("problem_no"),
        "practice_run_no": h.get("practice_run_no"),
        "case_code": h.get("case_code"),
        "created_at_utc": h.get("created_at_utc"),
        "compression": h.get("compression"),
        "content_encryption": h.get("content_encryption"),
        "frame_plaintext_target_bytes": h.get("frame_plaintext_target_bytes"),
        "nonce_prefix_b64": h.get("nonce_prefix_b64"),
        "wrapped_key_ids": [w.get("key_id") for w in (h.get("wrapped_deks") or [])],
        "n_frames": len(env.frames),
        "frames": env.frames,
        "trailing_len": env.trailing_len,
        "signature_b64": base64.b64encode(env.signature).decode("ascii"),
    }


def _walk(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.glob("*.jlog")))
            out.extend(sorted(p.glob("*.psum")))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="解析官方 .jlog / .psum 信封 metadata，并在有 wrap 私钥时解密正文")
    ap.add_argument("paths", nargs="+", help="文件或目录（目录会取 *.jlog / *.psum）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认逐行摘要）")
    ap.add_argument("--private-key", type=Path, default=None, help="可选：wrap 私钥 PEM")
    ap.add_argument("--key-id", default=None, help="可选：只尝试指定 key_id")
    ap.add_argument("--out-dir", type=Path, default=None, help="解密正文写入目录")
    args = ap.parse_args(argv)

    files = _walk([Path(p) for p in args.paths])
    summaries: list[dict[str, Any]] = []
    for p in files:
        try:
            env = parse_envelope(p)
            summaries.append(envelope_summary(env))
        except Exception as exc:  # noqa: BLE001
            summaries.append({"path": str(p), "error": f"{type(exc).__name__}: {exc}"})

    if args.private_key is not None:
        out_dir = args.out_dir or Path("decrypted")
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in files:
            try:
                plain = decrypt_envelope(p, args.private_key, key_id=args.key_id)
                target = out_dir / f"{p.stem}.payload.bin"
                target.write_bytes(plain)
                print(f"[decrypt] {p} -> {target} ({len(plain)} bytes)", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[decrypt-fail] {p}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        for s in summaries:
            if "error" in s:
                print(f"ERROR {s['path']}: {s['error']}")
                continue
            print(
                f"{s['case_code']}  run={s['practice_run_no']}  "
                f"P{s['problem_no']}  frames={s['n_frames']}  "
                f"compressed={sum(f['plaintext_len'] for f in s['frames'])}  "
                f"cipher={sum(f['cipher_len'] for f in s['frames'])}  "
                f"created={s['created_at_utc']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
