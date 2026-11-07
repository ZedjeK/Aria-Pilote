// Lanceur d'Aria Pilote : démarre l'appli sans console (Python portable du dossier),
// attend qu'elle réponde, puis ouvre l'interface dans le navigateur par défaut.
// Compilé par tools/build_portable.py avec le csc.exe fourni par Windows.
//
// Exe portable (build_portable.py --exe) : l'appli est embarquée à la fin de l'exe, archive zip suivie d'un pied
// de 16 octets (position de l'archive sur 8 octets, puis « ARIAPAK1 »). Au lancement, elle est installée ou mise à
// jour dans %USERPROFILE%\.Aria-Pilote (--home DOSSIER pour un autre), sans jamais toucher à tes affaires (models,
// data, config.json…) ni aux moteurs déjà installés. Sans archive, l'appli rangée à côté de l'exe est lancée.
//
// L'installation recopie aussi la seule partie lanceur (quelques dizaines de Ko, sans l'archive) dans
// .Aria-Pilote\Aria-Pilote.exe, et la première crée un raccourci « Aria Pilote » sur le bureau et dans le menu Démarrer.
// Le raccourci reste valable d'une version à l'autre, et l'exe téléchargé peut être supprimé après l'installation.

using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class Launcher
{
    const int DefaultPort = 8700;
    const int StartTimeoutSeconds = 90;
    const int StopTimeoutSeconds = 30;
    const string Magic = "ARIAPAK1";
    const string VersionFile = ".version";
    const string LauncherName = "Aria-Pilote.exe";
    const string ShortcutName = "Aria Pilote.lnk";
    // Présent une fois les raccourcis créés : un raccourci que tu as supprimé n'est pas recréé à la mise à jour
    const string ShortcutsDone = ".raccourcis";
    const int NoteSeconds = 5;  // la fenêtre de démarrage reste au moins ce temps quand elle a quelque chose à dire
    // Le code de l'appli, remplacé à chaque mise à jour ; tout le reste du dossier t'appartient
    static readonly string[] CodeItems = { "python", "aria_pilote", "tools", "services", "requirements.txt" };
    // Gardés d'une version à l'autre, même à l'intérieur du code
    static readonly string[] KeptFiles = { @"services\searxng\settings.yml" };
    const string SettingsModel = @"services\searxng\settings.yml.modele";

    [STAThread]
    static int Main(string[] args)
    {
        string exe = Application.ExecutablePath;
        string root = AppDomain.CurrentDomain.BaseDirectory;
        // --install-only : installe ou met à jour, sans fenêtre ni lancement (tests de build_portable)
        bool installOnly = Array.IndexOf(args, "--install-only") >= 0;
        string note = null;
        try
        {
            using (var payload = Payload.Open(exe))
            {
                if (payload != null)
                {
                    root = HomeDir(args);
                    using (var mutex = new Mutex(false, "AriaPiloteLanceur"))
                    {
                        mutex.WaitOne();
                        try
                        {
                            Version before = InstalledVersion(root);
                            Install(payload, root, !installOnly);
                            // Ni le petit lanceur ni les raccourcis ne doivent faire échouer une installation réussie
                            string launcher = Quietly(() => PlaceLauncher(exe, payload.Start, root), installOnly);
                            bool shortcuts = launcher != null
                                && Quietly(() => CreateShortcuts(root, launcher, args), installOnly) == "créés";
                            note = Note(before, payload.Version, shortcuts);
                        }
                        finally { mutex.ReleaseMutex(); }
                    }
                }
            }
        }
        catch (Exception e)
        {
            if (installOnly)
            {
                using (var err = new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)))
                    err.WriteLine(e.Message);
            }
            else Fail("Installation impossible :\n" + e.Message);
            return 1;
        }
        return installOnly ? 0 : Start(root, note);
    }

    static string Arg(string[] args, string name)
    {
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == name) return args[i + 1];
        return null;
    }

    // Une étape facultative : son échec est signalé (sur la sortie d'erreur avec --install-only), pas fatal
    static string Quietly(Func<string> step, bool installOnly)
    {
        try { return step(); }
        catch (Exception e)
        {
            if (installOnly)
            {
                using (var err = new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)))
                    err.WriteLine("avertissement : " + e.Message);
            }
            return null;
        }
    }

    static string Note(Version before, Version installed, bool shortcuts)
    {
        if (before != null && before >= installed) return null;
        string what = before == null
            ? "Aria Pilote " + installed + " est installée."
              + (shortcuts ? "\nRaccourcis « Aria Pilote » créés sur le bureau et dans le menu Démarrer." : "")
            : "Aria Pilote est passée en version " + installed + ".";
        return what + "\nTu peux supprimer l'exe téléchargé : il ne sert plus.";
    }

    static string HomeDir(string[] args)
    {
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == "--home") return Path.GetFullPath(args[i + 1]);
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".Aria-Pilote");
    }

    static int Start(string root, string note)
    {
        string url = Url(root);
        if (!IsAlive(url))
        {
            string pythonw = Path.Combine(root, "python", "pythonw.exe");
            if (!File.Exists(pythonw))
            {
                Fail("Python portable introuvable :\n" + pythonw);
                return 1;
            }
            var psi = new ProcessStartInfo(pythonw, "-m aria_pilote");
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            Process.Start(psi);

            // Le démarrage prend de quelques secondes à une minute et demie : une fenêtre le dit, plutôt que rien
            bool alive = false;
            var form = new ProgressForm("Aria Pilote", note);
            form.Shown += (s, e) => form.Report("Démarrage d'Aria Pilote…", -1);
            WithWindow(form, () =>
            {
                DateTime shown = DateTime.Now;
                DateTime deadline = shown.AddSeconds(StartTimeoutSeconds);
                while (!(alive = IsAlive(url)) && DateTime.Now < deadline) Thread.Sleep(500);
                if (note != null && !form.HiddenByUser)
                {
                    int left = NoteSeconds * 1000 - (int)(DateTime.Now - shown).TotalMilliseconds;
                    if (alive && left > 0) Thread.Sleep(left);  // le temps de lire la note
                }
            });
            if (!alive)
            {
                Fail("Aria Pilote ne répond pas.\nRegarde le journal : " + Path.Combine(root, "data", "aria_pilote.log"));
                return 1;
            }
        }

        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        return 0;
    }

    // Un travail dans un fil, accompagné de sa fenêtre de progression. On rend la main quand le travail est fini, jamais
    // avant : fermer la fenêtre (Alt+F4, barre des tâches) ne fait que la masquer, et si elle disparaît quand même
    // (arrêt de Windows…), on attend la fin du travail sans elle.
    internal static void WithWindow(ProgressForm form, Action work)
    {
        Thread worker = null;
        form.Shown += (s, e) =>
        {
            worker = new Thread(() =>
            {
                try { work(); }
                finally { form.Finish(); }
            }) { IsBackground = true };
            worker.Start();
        };
        Application.Run(form);
        if (worker != null) worker.Join();
        else work();  // fenêtre fermée avant même d'être affichée
    }

    // --- Petit lanceur et raccourcis ---------------------------------------------------

    // La partie lanceur de l'exe (tout ce qui précède l'archive) dans le dossier d'installation. Renvoie son chemin.
    static string PlaceLauncher(string exe, long launcherLength, string home)
    {
        string target = Path.Combine(home, LauncherName);
        var bytes = new byte[launcherLength];
        using (var source = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read))
            Payload.ReadExactly(source, bytes);
        if (File.Exists(target) && SameBytes(File.ReadAllBytes(target), bytes)) return target;
        string fresh = target + ".neuf";
        File.WriteAllBytes(fresh, bytes);
        if (File.Exists(target)) File.Delete(target);
        File.Move(fresh, target);
        return target;
    }

    static bool SameBytes(byte[] a, byte[] b)
    {
        if (a.Length != b.Length) return false;
        for (int i = 0; i < a.Length; i++)
            if (a[i] != b[i]) return false;
        return true;
    }

    // Une seule fois : sur le bureau et dans le menu Démarrer. Pas pour une installation ailleurs (--home), sauf
    // dossiers donnés pour les tests (--desktop et --start-menu). Renvoie « créés », « déjà faits » ou « aucun ».
    static string CreateShortcuts(string home, string launcher, string[] args)
    {
        string desktop = Arg(args, "--desktop"), startMenu = Arg(args, "--start-menu");
        bool forTests = desktop != null && startMenu != null;
        if (Arg(args, "--home") != null && !forTests) return "aucun";
        string done = Path.Combine(home, ShortcutsDone);
        if (File.Exists(done)) return "déjà faits";
        if (!forTests)
        {
            desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            startMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
        }
        foreach (string folder in new[] { desktop, startMenu })
        {
            Directory.CreateDirectory(folder);
            WriteShortcut(Path.Combine(folder, ShortcutName), launcher, home);
        }
        File.WriteAllText(done, desktop + "\n" + startMenu + "\n");
        return "créés";
    }

    // Un .lnk par l'objet COM de Windows (WScript.Shell), sans dépendance à compiler
    static void WriteShortcut(string path, string target, string workingDir)
    {
        Type type = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(type);
        try
        {
            object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            try
            {
                Type lt = link.GetType();
                lt.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { target });
                lt.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { workingDir });
                lt.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] { target + ",0" });
                lt.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { "Lancer Aria Pilote" });
                lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            }
            finally { Marshal.FinalReleaseComObject(link); }
        }
        finally { Marshal.FinalReleaseComObject(shell); }
    }

    // --- Installation et mise à jour ---------------------------------------------------

    static void Install(Payload payload, string home, bool window)
    {
        Version installed = InstalledVersion(home);
        if (installed != null && installed >= payload.Version)
            return;  // à jour, ou plus récente (un exe plus ancien ne la remplace pas)

        // Seule l'appli de ce dossier est arrêtée : une autre qui répondrait sur le même port n'est pas concernée
        string url = Url(home);
        if (Directory.Exists(Path.Combine(home, "aria_pilote")) && IsAlive(url)) Stop(url);

        bool engines = !HasEntries(Path.Combine(home, "engines"));
        if (!window)
        {
            Deploy(payload, home, engines, (text, percent) => { });
            return;
        }
        string title = (installed == null ? "Installation" : "Mise à jour") + " d'Aria Pilote " + payload.Version;
        Exception error = null;
        // Fenêtre fermée en cours de copie : l'installation va quand même au bout, sans être coupée à mi-échange
        var form = new ProgressForm(title);
        WithWindow(form, () =>
        {
            try { Deploy(payload, home, engines, form.Report); }
            catch (Exception ex) { error = ex; }
        });
        if (error != null) throw error;
    }

    static void Deploy(Payload payload, string home, bool engines, Action<string, int> report)
    {
        Directory.CreateDirectory(home);
        string staging = Path.Combine(home, ".maj");
        string old = Path.Combine(home, ".ancien");
        DeleteDir(staging);
        DeleteDir(old);

        // 1. L'archive dans un dossier à part : rien n'est touché tant qu'elle n'est pas entière
        payload.Extract(staging, engines, report);
        foreach (string kept in KeptFiles)
        {
            string mine = Path.Combine(home, kept);
            if (File.Exists(mine)) File.Copy(mine, Path.Combine(staging, kept), true);
        }

        // 2. L'échange : l'ancien code mis de côté, le nouveau à sa place ; au moindre blocage, retour arrière
        report("Mise en place…", -1);
        Directory.CreateDirectory(old);
        try
        {
            foreach (string item in CodeItems)
            {
                string current = Path.Combine(home, item);
                if (Exists(current)) Move(current, Path.Combine(old, item));
            }
            foreach (string item in CodeItems)
            {
                string fresh = Path.Combine(staging, item);
                if (Exists(fresh)) Move(fresh, Path.Combine(home, item));
            }
        }
        catch (Exception e)
        {
            if (!(e is IOException || e is UnauthorizedAccessException)) throw;
            foreach (string item in CodeItems)
            {
                string placed = Path.Combine(home, item);
                string saved = Path.Combine(old, item);
                if (!Exists(saved)) continue;
                if (Exists(placed)) DeleteItem(placed);
                Move(saved, placed);
            }
            throw new IOException("un fichier d'Aria Pilote est encore utilisé (" + e.Message + ").\n"
                                  + "Quitte Aria Pilote, puis relance l'exe : l'ancienne version est restée en place.");
        }
        string newEngines = Path.Combine(staging, "engines");
        if (engines && Directory.Exists(newEngines))
        {
            DeleteDir(Path.Combine(home, "engines"));  // vide, sinon on ne serait pas là
            Move(newEngines, Path.Combine(home, "engines"));
        }
        foreach (string folder in new[] { "data", "models" })
            Directory.CreateDirectory(Path.Combine(home, folder));
        WriteSettings(home);

        // 3. Le numéro de version en dernier : une installation interrompue sera reprise au prochain lancement
        File.WriteAllText(Path.Combine(home, VersionFile), payload.Version.ToString());
        DeleteDir(old);
        DeleteDir(staging);
    }

    // La configuration de SearXNG, avec une clé secrète propre à ce PC (jamais celle de l'exe)
    static void WriteSettings(string home)
    {
        string settings = Path.Combine(home, KeptFiles[0]);
        string model = Path.Combine(home, SettingsModel);
        if (File.Exists(settings) || !File.Exists(model)) return;
        var bytes = new byte[32];
        using (var rng = new RNGCryptoServiceProvider()) rng.GetBytes(bytes);
        string secret = BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        File.WriteAllText(settings, File.ReadAllText(model).Replace("{secret}", secret), new UTF8Encoding(false));
    }

    static Version InstalledVersion(string home)
    {
        string file = Path.Combine(home, VersionFile);
        if (File.Exists(file))
        {
            Version v;
            if (Version.TryParse(File.ReadAllText(file).Trim(), out v)) return v;
        }
        return null;
    }

    static void Stop(string url)
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(url + "api/shutdown");
            request.Method = "POST";
            request.Headers["Origin"] = url.TrimEnd('/');
            request.ContentLength = 0;
            request.Timeout = 5000;
            using (request.GetResponse()) { }
        }
        catch (WebException) { }
        DateTime deadline = DateTime.Now.AddSeconds(StopTimeoutSeconds);
        while (IsAlive(url))
        {
            if (DateTime.Now > deadline)
                throw new IOException("Aria Pilote tourne encore et ne s'arrête pas : quitte-le, puis relance l'exe.");
            Thread.Sleep(500);
        }
        Thread.Sleep(2000);  // le temps que le moteur et SearXNG lâchent leurs fichiers
    }

    static bool Exists(string path) { return File.Exists(path) || Directory.Exists(path); }

    static bool HasEntries(string dir)
    {
        return Directory.Exists(dir) && Directory.GetFileSystemEntries(dir).Length > 0;
    }

    static void Move(string from, string to)
    {
        if (Directory.Exists(from)) Directory.Move(from, to);
        else File.Move(from, to);
    }

    static void DeleteItem(string path)
    {
        if (Directory.Exists(path)) DeleteDir(path);
        else File.Delete(path);
    }

    static void DeleteDir(string dir)
    {
        if (!Directory.Exists(dir)) return;
        foreach (string file in Directory.GetFiles(dir, "*", SearchOption.AllDirectories))
            File.SetAttributes(file, FileAttributes.Normal);  // les fichiers en lecture seule bloquent Delete
        Directory.Delete(dir, true);
    }

    // --- Appli en marche ------------------------------------------------------------------

    static string Url(string root)
    {
        return "http://127.0.0.1:" + ReadPort(root) + "/";
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
        MessageBox.Show(message, "Aria Pilote", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

// L'archive embarquée à la fin de l'exe
sealed class Payload : IDisposable
{
    readonly FileStream file;
    readonly ZipArchive zip;
    public readonly Version Version;
    public readonly long Start;  // où commence l'archive : c'est aussi la taille du lanceur

    Payload(FileStream file, long start, long length)
    {
        this.file = file;
        Start = start;
        zip = new ZipArchive(new Window(file, start, length), ZipArchiveMode.Read);
        ZipArchiveEntry entry = zip.GetEntry("VERSION");
        if (entry == null) throw new InvalidDataException("archive embarquée sans numéro de version.");
        using (var reader = new StreamReader(entry.Open())) Version = Version.Parse(reader.ReadToEnd().Trim());
    }

    // null : pas d'archive (exe du mode dossier)
    public static Payload Open(string exe)
    {
        var file = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read);
        try
        {
            if (file.Length < 16) { file.Dispose(); return null; }
            var footer = new byte[16];
            file.Seek(-16, SeekOrigin.End);
            ReadFully(file, footer);
            if (Encoding.ASCII.GetString(footer, 8, 8) != "ARIAPAK1") { file.Dispose(); return null; }
            long start = BitConverter.ToInt64(footer, 0);
            return new Payload(file, start, file.Length - 16 - start);
        }
        catch
        {
            file.Dispose();
            throw;
        }
    }

    public void Extract(string dest, bool engines, Action<string, int> report)
    {
        string root = Path.GetFullPath(dest) + Path.DirectorySeparatorChar;
        long total = 0, done = 0;
        foreach (ZipArchiveEntry e in zip.Entries)
            if (Wanted(e, engines)) total += e.Length;
        var buffer = new byte[1 << 20];
        int lastPercent = -1;
        foreach (ZipArchiveEntry e in zip.Entries)
        {
            if (!Wanted(e, engines)) continue;
            string target = Path.GetFullPath(Path.Combine(dest, e.FullName.Replace('/', '\\')));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("chemin hors du dossier dans l'archive : " + e.FullName);
            if (e.FullName.EndsWith("/"))
            {
                Directory.CreateDirectory(target);
                continue;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            using (Stream input = e.Open())
            using (var output = new FileStream(target, FileMode.Create, FileAccess.Write))
            {
                int n;
                while ((n = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    output.Write(buffer, 0, n);
                    done += n;
                    int percent = total > 0 ? (int)(done * 100 / total) : 100;
                    if (percent != lastPercent)
                    {
                        lastPercent = percent;
                        report("Copie des fichiers… " + percent + " %", percent);
                    }
                }
            }
        }
    }

    static bool Wanted(ZipArchiveEntry e, bool engines)
    {
        return e.FullName != "VERSION" && (engines || !e.FullName.StartsWith("engines/"));
    }

    public static void ReadExactly(Stream s, byte[] buffer) { ReadFully(s, buffer); }

    static void ReadFully(Stream s, byte[] buffer)
    {
        int read = 0;
        while (read < buffer.Length)
        {
            int n = s.Read(buffer, read, buffer.Length - read);
            if (n == 0) throw new EndOfStreamException();
            read += n;
        }
    }

    public void Dispose()
    {
        zip.Dispose();
        file.Dispose();
    }

    // Une partie du fichier vue comme un flux à part entière : l'archive commence là où finit le lanceur
    sealed class Window : Stream
    {
        readonly Stream inner;
        readonly long start, length;
        long position;

        public Window(Stream inner, long start, long length)
        {
            this.inner = inner;
            this.start = start;
            this.length = length;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return length; } }
        public override long Position { get { return position; } set { position = value; } }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long left = length - position;
            if (left <= 0) return 0;
            if (count > left) count = (int)left;
            inner.Seek(start + position, SeekOrigin.Begin);
            int n = inner.Read(buffer, offset, count);
            position += n;
            return n;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            if (origin == SeekOrigin.Begin) position = offset;
            else if (origin == SeekOrigin.Current) position += offset;
            else position = length + offset;
            return position;
        }

        public override void Flush() { }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }
}

// La petite fenêtre de progression pendant l'installation
sealed class ProgressForm : Form
{
    readonly Label label;
    readonly ProgressBar bar;

    public ProgressForm(string title, string note = null)
    {
        Text = title;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        ControlBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new System.Drawing.Size(420, 90);
        try { Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
        label = new Label { Left = 16, Top = 14, Width = 388, Text = "Préparation…" };
        bar = new ProgressBar { Left = 16, Top = 44, Width = 388, Height = 22, Maximum = 100 };
        Controls.Add(label);
        Controls.Add(bar);
        if (note != null)
        {
            // Ce qui vient d'être fait (installation, raccourcis) : sous la barre, sur deux ou trois lignes
            var info = new Label { Left = 16, Top = 76, Width = 388, Height = 54, Text = note };
            Controls.Add(info);
            ClientSize = new System.Drawing.Size(420, 140);
        }
    }

    // Vrai une fois la fenêtre fermée par l'utilisateur : elle n'est plus que masquée (voir Launcher.WithWindow)
    public bool HiddenByUser { get { return hiddenByUser; } }
    volatile bool hiddenByUser;
    volatile bool finished;

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        // Avant la fin du travail, la fermer ne fait que la masquer : le travail continue et dira lui-même comment il
        // s'est terminé. Les autres fermetures (arrêt de Windows…) passent.
        if (!finished && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            hiddenByUser = true;
            Hide();
        }
        base.OnFormClosing(e);
    }

    // Appelé par le fil de travail quand il a fini : ferme la fenêtre, ce qui rend la main à Application.Run
    public void Finish()
    {
        finished = true;
        Post(Close);
    }

    // percent < 0 : barre animée, sans pourcentage
    public void Report(string text, int percent)
    {
        Post(() =>
        {
            label.Text = text;
            if (percent < 0) bar.Style = ProgressBarStyle.Marquee;
            else
            {
                bar.Style = ProgressBarStyle.Continuous;
                bar.Value = Math.Min(100, percent);
            }
        });
    }

    // Depuis le fil de travail. La fenêtre a pu être détruite entre-temps : il n'y a plus rien à afficher, et surtout
    // pas d'exception, qui ferait planter le lanceur depuis un fil d'arrière-plan.
    void Post(Action action)
    {
        try
        {
            if (IsHandleCreated && !IsDisposed) BeginInvoke(action);
        }
        catch (InvalidOperationException) { }  // détruite entre le test et l'appel (ObjectDisposedException en dérive)
    }
}
