// portrelay —— 极简 TCP 转发（Windows，无需外部依赖）
//
// 用法:
//   portrelay.exe <监听地址> <监听端口> <目标1>[,<目标2>,...]
//   目标写法: host:port
//
// 例（模拟器端口可能被改成 2025 或 2026，两个都挂上，自动选能连上的那个）:
//   portrelay.exe 192.168.122.161 2026 127.0.0.1:2025,127.0.0.1:2026
// 也兼容老写法:
//   portrelay.exe 192.168.122.161 2026 127.0.0.1 2025
//
// 只绑定指定地址（不绑 0.0.0.0），因此不会与模拟器绑定的 127.0.0.1/[::1] 冲突。
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

internal static class PortRelay
{
    private static readonly object LogLock = new object();
    private static string _logPath = "portrelay.log";
    private static string[] _targets = new string[] { "127.0.0.1:2026" };
    private static volatile string _active = null;
    private const int ConnectTimeoutMs = 1500;

    private static void Log(string msg)
    {
        try
        {
            lock (LogLock)
            {
                File.AppendAllText(_logPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + msg + Environment.NewLine,
                    Encoding.UTF8);
            }
        }
        catch { }
    }

    private static int Main(string[] args)
    {
        string listenAddr = args.Length > 0 ? args[0] : "192.168.122.161";
        int listenPort = args.Length > 1 ? int.Parse(args[1]) : 2026;
        if (args.Length > 3)
        {
            _targets = new string[] { args[2] + ":" + args[3] };   // 老写法
        }
        else if (args.Length > 2)
        {
            _targets = args[2].Split(',');                          // 新写法（可多目标）
        }
        for (int i = 0; i < _targets.Length; i++) { _targets[i] = _targets[i].Trim(); }

        try
        {
            string exePath = System.Reflection.Assembly.GetExecutingAssembly().Location;
            _logPath = Path.Combine(Path.GetDirectoryName(exePath), "portrelay.log");
        }
        catch { }

        IPAddress lip;
        if (!IPAddress.TryParse(listenAddr, out lip))
        {
            Log("bad listen address: " + listenAddr);
            return 2;
        }

        TcpListener listener = null;
        while (listener == null)
        {
            try
            {
                listener = new TcpListener(lip, listenPort);
                listener.Start(128);
            }
            catch (Exception ex)
            {
                listener = null;
                Log("bind " + listenAddr + ":" + listenPort + " failed: " + ex.Message + " (retry in 5s)");
                Thread.Sleep(5000);
            }
        }
        Log("listening " + listenAddr + ":" + listenPort + " -> [" + string.Join(", ", _targets) + "]"
            + " (pid " + System.Diagnostics.Process.GetCurrentProcess().Id + ")");

        while (true)
        {
            TcpClient client;
            try
            {
                client = listener.AcceptTcpClient();
            }
            catch (Exception ex)
            {
                Log("accept failed: " + ex.Message);
                Thread.Sleep(500);
                continue;
            }
            ThreadPool.QueueUserWorkItem(new WaitCallback(Relay), client);
        }
    }

    /// 依次尝试所有候选目标，返回第一个连上的连接
    private static TcpClient ConnectAny()
    {
        string lastErr = "no target";
        foreach (string t in _targets)
        {
            int idx = t.LastIndexOf(':');
            if (idx <= 0) { lastErr = "bad target " + t; continue; }
            string host = t.Substring(0, idx);
            int port;
            if (!int.TryParse(t.Substring(idx + 1), out port)) { lastErr = "bad port in " + t; continue; }

            TcpClient c = new TcpClient();
            try
            {
                c.NoDelay = true;
                IAsyncResult ar = c.BeginConnect(host, port, null, null);
                if (!ar.AsyncWaitHandle.WaitOne(ConnectTimeoutMs) || !c.Connected)
                {
                    try { c.Close(); } catch { }
                    lastErr = "timeout " + t;
                    continue;
                }
                c.EndConnect(ar);
                if (_active != t)
                {
                    _active = t;
                    Log("upstream target -> " + t);
                }
                return c;
            }
            catch (Exception ex)
            {
                lastErr = t + ": " + ex.Message;
                try { c.Close(); } catch { }
            }
        }
        throw new Exception(lastErr);
    }

    private static void Relay(object state)
    {
        TcpClient client = (TcpClient)state;
        TcpClient upstream = null;
        try
        {
            client.NoDelay = true;
            upstream = ConnectAny();

            NetworkStream cs = client.GetStream();
            NetworkStream us = upstream.GetStream();

            TcpClient c2 = client;
            TcpClient u2 = upstream;
            Thread t = new Thread(delegate()
            {
                try { us.CopyTo(cs); }
                catch { }
                finally
                {
                    try { c2.Close(); } catch { }
                    try { u2.Close(); } catch { }
                }
            });
            t.IsBackground = true;
            t.Start();

            try { cs.CopyTo(us); }
            catch { }
            try { upstream.Close(); } catch { }
        }
        catch (Exception ex)
        {
            Log("no upstream available: " + ex.Message);
            try { client.Close(); } catch { }
            if (upstream != null) { try { upstream.Close(); } catch { } }
        }
    }
}
