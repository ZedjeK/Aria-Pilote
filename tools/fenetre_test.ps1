# Fenêtre de test pour le contrôle de l'écran : contrôles Windows natifs, ouverte sans prendre le focus,
# puis envoyée derrière toutes les autres (PrintWindow la capture quand même).
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public class FenetreTest : Form {
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override void OnShown(EventArgs e) {
        base.OnShown(e);
        SetWindowPos(Handle, new IntPtr(1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010); // HWND_BOTTOM, sans bouger ni activer
    }
}
"@
$form = New-Object FenetreTest
$form.Text = "Aria - fenetre de test"
$form.StartPosition = "Manual"
$form.Location = New-Object System.Drawing.Point(200, 200)
$form.ClientSize = New-Object System.Drawing.Size(520, 300)
$font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.Font = $font

$label = New-Object System.Windows.Forms.Label
$label.Text = "Erreur 0x80070005 : acces refuse au dossier C:\Partage\Compta"
$label.Location = New-Object System.Drawing.Point(20, 20); $label.AutoSize = $true

$nameLabel = New-Object System.Windows.Forms.Label
$nameLabel.Text = "Nom du fichier"; $nameLabel.Location = New-Object System.Drawing.Point(20, 65); $nameLabel.AutoSize = $true
$name = New-Object System.Windows.Forms.TextBox
$name.Name = "Nom du fichier"; $name.AccessibleName = "Nom du fichier"
$name.Text = "rapport-octobre.xlsx"; $name.Location = New-Object System.Drawing.Point(150, 62); $name.Width = 330

$pwdLabel = New-Object System.Windows.Forms.Label
$pwdLabel.Text = "Mot de passe"; $pwdLabel.Location = New-Object System.Drawing.Point(20, 105); $pwdLabel.AutoSize = $true
$pwd = New-Object System.Windows.Forms.TextBox
$pwd.AccessibleName = "Mot de passe"; $pwd.UseSystemPasswordChar = $true; $pwd.Text = "NePasLire123"
$pwd.Location = New-Object System.Drawing.Point(150, 102); $pwd.Width = 330

$check = New-Object System.Windows.Forms.CheckBox
$check.Text = "Activer la sauvegarde"; $check.Checked = $true
$check.Location = New-Object System.Drawing.Point(20, 145); $check.AutoSize = $true

$retry = New-Object System.Windows.Forms.Button
$retry.Text = "Reessayer"; $retry.Location = New-Object System.Drawing.Point(280, 230); $retry.Width = 100
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Annuler"; $cancel.Location = New-Object System.Drawing.Point(390, 230); $cancel.Width = 100
$off = New-Object System.Windows.Forms.Button
$off.Text = "Exporter"; $off.Enabled = $false; $off.Location = New-Object System.Drawing.Point(20, 230); $off.Width = 100

$form.Controls.AddRange(@($label, $nameLabel, $name, $pwdLabel, $pwd, $check, $retry, $cancel, $off))
[System.Windows.Forms.Application]::Run($form)
