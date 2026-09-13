// portrelay —— 极简 TCP 转发（Windows，无外部依赖）
// 用法: portrelay.exe <监听地址> <监听端口> <目标地址> <目标端口>
//   portrelay.exe 192.168.122.161 2026 127.0.0.1 2026
// 说明: 只绑定指定地址（不绑 0.0.0.0），因此不会与模拟器绑定的 127.0.0.1/[::1]:2026 冲突。
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

internal static class PortRelay
{
    private static readonly object LogLock = new object();
    private static string _logPath = "portrelay.log";
    private static string _targetAddr = "127.0.0.1";
    private static int _targetPort = 2026;

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
        _targetAddr = args.Length > 2 ? args[2] : "127.0.0.1";
        _targetPort = args.Length > 3 ? int.Parse(args[3]) : 2026;

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
        Log("listening " + listenAddr + ":" + listenPort + " -> " + _targetAddr + ":" + _targetPort
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

    private static void Relay(object state)
    {
        TcpClient client = (TcpClient)state;
        TcpClient upstream = null;
        try
        {
            client.NoDelay = true;
            upstream = new TcpClient();
            upstream.NoDelay = true;
            upstream.Connect(_targetAddr, _targetPort);

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
            Log("upstream " + _targetAddr + ":" + _targetPort + " failed: " + ex.Message);
            try { client.Close(); } catch { }
            if (upstream != null) { try { upstream.Close(); } catch { } }
        }
    }
}
