// A WPF window for the tests of the guide v2's elements on a Windows desktop (src/capture/uia_win.rs,
// uia_wpf): tabs whose selected one holds a button and a password box of a known colour. WPF puts a
// tab's content under its tab in the tree. Compiled there by the csc.exe of the .NET Framework that
// comes with Windows: nothing downloaded. ASCII only.
//
//   atelier_wpf.exe <title> <x> <y> <w> <h>     (in pixels of 96 dpi, as WPF counts)
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

static class AtelierWpf
{
    [STAThread]
    static int Main(string[] a)
    {
        Application app = new Application();
        Window w = new Window();
        w.Title = a[0];
        w.WindowStartupLocation = WindowStartupLocation.Manual;
        w.Left = double.Parse(a[1], CultureInfo.InvariantCulture);
        w.Top = double.Parse(a[2], CultureInfo.InvariantCulture);
        w.Width = double.Parse(a[3], CultureInfo.InvariantCulture);
        w.Height = double.Parse(a[4], CultureInfo.InvariantCulture);

        StackPanel facture = new StackPanel();
        facture.Margin = new Thickness(16);
        Button valider = new Button();
        valider.Content = "Valider la facture";
        valider.Width = 160;
        valider.Height = 36;
        valider.HorizontalAlignment = HorizontalAlignment.Left;
        PasswordBox secret = new PasswordBox();
        secret.Background = new SolidColorBrush(Color.FromRgb(0xff, 0x98, 0x00));
        secret.Width = 160;
        secret.Height = 24;
        secret.Margin = new Thickness(0, 16, 0, 0);
        secret.HorizontalAlignment = HorizontalAlignment.Left;
        secret.Password = "invente-0000";
        facture.Children.Add(valider);
        facture.Children.Add(secret);

        TabControl tabs = new TabControl();
        TabItem un = new TabItem();
        un.Header = "Facture";
        un.Content = facture;
        TabItem deux = new TabItem();
        deux.Header = "Clients";
        deux.Content = new TextBlock();
        tabs.Items.Add(un);
        tabs.Items.Add(deux);
        tabs.SelectedIndex = 0;
        w.Content = tabs;
        app.Run(w);
        return 0;
    }
}
