// testsrv —— 临时上游（用于验证 portrelay 是否真的把流量转到了目标端口）
using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

internal static class TestSrv
{
    private static int Main(string[] args)
    {
        int port = args.Length > 0 ? int.Parse(args[0]) : 12026;
        TcpListener l = new TcpListener(IPAddress.Loopback, port);
        l.Start(64);
        while (true)
        {
            TcpClient c = l.AcceptTcpClient();
            ThreadPool.QueueUserWorkItem(new WaitCallback(Handle), c);
        }
    }

    private static void Handle(object state)
    {
        TcpClient c = (TcpClient)state;
        try
        {
            NetworkStream st = c.GetStream();
            byte[] buf = new byte[4096];
            int n = st.Read(buf, 0, buf.Length);
            string req = Encoding.ASCII.GetString(buf, 0, n);
            string first = req.Split('\n')[0].Trim().Replace("\"", "'");
            string body = "{\"accepted\":false,\"marker\":\"TEST-UPSTREAM\",\"first\":\"" + first + "\"}";
            string resp = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + Encoding.UTF8.GetByteCount(body) + "\r\nConnection: close\r\n\r\n" + body;
            byte[] b = Encoding.UTF8.GetBytes(resp);
            st.Write(b, 0, b.Length);
            st.Flush();
        }
        catch { }
        finally { try { c.Close(); } catch { } }
    }
}
