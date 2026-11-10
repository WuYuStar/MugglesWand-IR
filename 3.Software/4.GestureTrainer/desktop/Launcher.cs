using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
using System.Reflection;
[assembly: AssemblyTitle("魔杖手势训练")]
[assembly: AssemblyProduct("MugglesWand Trainer")]
[assembly: AssemblyDescription("手势录制、数据检查、模型训练与魔杖升级")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
internal static class Launcher {
    static string Quote(string s) {
        var b = new StringBuilder("\""); int slashes=0;
        foreach(char c in s) {
            if(c=='\\') {slashes++;continue;}
            if(c=='"') {b.Append('\\',slashes*2+1);b.Append(c);slashes=0;continue;}
            b.Append('\\',slashes);slashes=0;b.Append(c);
        }
        b.Append('\\',slashes*2);b.Append('"');return b.ToString();
    }
    [STAThread] static int Main(string[] args) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string python=Path.Combine(root,"runtime","python.exe");
        string entry=Path.Combine(root,"app","desktop.py");
        try {
            if(!File.Exists(python)||!File.Exists(entry)) throw new IOException("软件文件不完整，请完整解压后再打开。请保留 app 和 runtime 文件夹。");
            var cmd=new StringBuilder(Quote(entry));
            foreach(string arg in args)cmd.Append(" ").Append(Quote(arg));
            var p=new ProcessStartInfo(python,cmd.ToString());
            p.WorkingDirectory=root;p.UseShellExecute=false;p.CreateNoWindow=true;
            // Keep the portable runtime independent of installed Python settings.
            p.EnvironmentVariables.Remove("PYTHONHOME");p.EnvironmentVariables.Remove("PYTHONPATH");
            p.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
            Process.Start(p);return 0;
        }catch(Exception e){MessageBox.Show(e.Message,"魔杖手势训练",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
}
