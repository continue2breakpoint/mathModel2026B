# 最终论文与支撑材料

- `paper.pdf`：按最终 q3-v18 / q4-v18 补全的论文。
- `main.tex`、`sections/`：可编辑正文，已补齐四问、证明、消融、演练和代码附录。
- `figures/`：6张可编辑SVG及对应嵌入PDF；数学图为几何示意，实验图由记录生成。
- `data/`、`tables/`：本文使用的实验数据、源文件哈希与自动生成表。
- `code/snapshot/`、`code/MANIFEST.md`：最终工作区源码快照与校验清单。
- `FINAL_CHECKLIST.md`：提交前仍需人工填入的正式测试信息及截图。
- `MODEL_NOTES.md`：论文与最终实现的保证边界。

从项目根目录编译：

```bash
python3 paper/make_assets.py
python3 paper/verify_formulas.py
bash paper/build.sh
```

`make_assets.py`首次优先读取`build/q3-final-ablation.json`，该文件来自本轮最终参数重跑；数据副本保存在`data/`。重新跑消融：

```bash
python3 script/q3_or_opt_ablation.py --json paper/build/q3-final-ablation.json
```

使用LuaLaTeX、babel和本机Noto CJK字体。图表生成依赖numpy、matplotlib及已有框架依赖。正式测试须使用官方结果填表，演练数据不代填；本轮未调用官方接口或消耗正式测试机会。
