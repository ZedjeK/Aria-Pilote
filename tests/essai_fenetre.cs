// Essai de la fenêtre de progression du lanceur (voir test_exe_portable.py) : compilé avec tools/launcher/Launcher.cs,
// avec ce point d'entrée à la place du sien. Pendant un travail d'une seconde et demie, la fenêtre est fermée comme
// le ferait l'utilisateur (« fermee »), ou détruite comme à l'arrêt de Windows (« detruite »). Le travail doit aller
// au bout, WithWindow ne rendre la main qu'après lui, et rien ne doit planter.

using System;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;

static class EssaiFenetre
{
    const int WorkMilliseconds = 1500;

    [STAThread]
    static int Main(string[] args)
    {
        string scenario = args[0];
        var form = new ProgressForm("Essai du lanceur");
        form.Opacity = 0;  // rien à l'écran pendant les tests
        form.ShowInTaskbar = false;
        form.Shown += (s, e) =>
        {
            var timer = new System.Windows.Forms.Timer { Interval = 200 };
            timer.Tick += (s2, e2) =>
            {
                timer.Stop();
                if (scenario == "fermee") form.Close();
                else form.Dispose();
            };
            timer.Start();
        };
        bool done = false, hidden = false;
        var watch = Stopwatch.StartNew();
        Launcher.WithWindow(form, () =>
        {
            for (int i = 0; i < WorkMilliseconds / 100; i++)
            {
                form.Report("Essai " + i, i);
                Thread.Sleep(100);
            }
            hidden = form.HiddenByUser;
            done = true;
        });
        // Laisse aux appels en attente le temps de passer : un BeginInvoke sur la fenêtre détruite planterait ici
        Thread.Sleep(300);
        Console.WriteLine("fini=" + done + " masquee=" + hidden + " attendu=" + (watch.ElapsedMilliseconds >= WorkMilliseconds));
        return 0;
    }
}
