// Lanceur d'Aria Home : démarre l'appli sans console (Python portable du dossier),
// attend qu'elle réponde, puis ouvre l'interface dans le navigateur par défaut.
// Compilé par tools/build_portable.py avec le csc.exe fourni par Windows.

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class Launcher
{
    const int DefaultPort = 8700;
    const int StartTimeoutSeconds = 90;

    [STAThread]
    static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string url = "http://127.0.0.1:" + ReadPort(root) + "/";

        if (!IsAlive(url))
        {
            string pythonw = Path.Combine(root, "python", "pythonw.exe");
            if (!File.Exists(pythonw))
            {
                Fail("Python portable introuvable :\n" + pythonw);
                return 1;
            }
            var psi = new ProcessStartInfo(pythonw, "-m aria_home");
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            Process.Start(psi);

            DateTime deadline = DateTime.Now.AddSeconds(StartTimeoutSeconds);
            while (!IsAlive(url))
            {
                if (DateTime.Now > deadline)
                {
                    Fail("Aria Home ne répond pas.\nRegarde le journal : " + Path.Combine(root, "data", "aria_home.log"));
                    return 1;
                }
                Thread.Sleep(500);
            }
        }

        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        return 0;
    }

    static int ReadPort(string root)
    {
        try
        {
            string config = File.ReadAllText(Path.Combine(root, "config.json"));
            Match m = Regex.Match(config, "\"app_port\"\\s*:\\s*(\\d+)");
            if (m.Success) return int.Parse(m.Groups[1].Value);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return DefaultPort;
    }

    static bool IsAlive(string url)
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(url + "api/status");
            request.Timeout = 1000;
            using (var response = (HttpWebResponse)request.GetResponse())
            {
                return response.StatusCode == HttpStatusCode.OK;
            }
        }
        catch (WebException)
        {
            return false;
        }
    }

    static void Fail(string message)
    {
        MessageBox.Show(message, "Aria Home", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
