using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Versions;
using CUE4Parse.UE4.VirtualFileSystem;
using CUE4Parse_Conversion;

if (args.Length != 4)
{
    Console.Error.WriteLine("Usage: PalworldMeshExport <pak-directory> <mapping.usmap> <output-directory> <skeletal-mesh.uasset>");
    return 2;
}

var pakDirectory = Path.GetFullPath(args[0]);
var mapping = Path.GetFullPath(args[1]);
var outputDirectory = Path.GetFullPath(args[2]);
var requestedPath = args[3];
Directory.CreateDirectory(outputDirectory);

using var provider = new DefaultFileProvider(
    pakDirectory,
    SearchOption.AllDirectories,
    new VersionContainer(EGame.GAME_UE5_1),
    StringComparer.OrdinalIgnoreCase);
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(mapping);
provider.Initialize();
IAesVfsReader? pak = provider.UnloadedVfs.FirstOrDefault(file =>
    file.Name.Contains("Pal-Windows.pak", StringComparison.OrdinalIgnoreCase));
if (pak is null)
{
    Console.Error.WriteLine($"Pal-Windows.pak was not found below {pakDirectory}");
    return 3;
}
pak.MountTo((FileProviderDictionary)provider.Files, provider.PathComparer);

static string NormalizePackagePath(string value)
{
    var path = value.Replace('\\', '/').TrimStart('/');
    if (path.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)) path = path[..^7];
    var dot = path.LastIndexOf('.');
    return dot > path.LastIndexOf('/') ? path[..dot] : path;
}

var packagePath = NormalizePackagePath(requestedPath);
try
{
    var package = await provider.LoadPackageAsync(packagePath);
    var mesh = package.GetExports().OfType<USkeletalMesh>().FirstOrDefault();
    if (mesh is null)
    {
        Console.Error.WriteLine("No USkeletalMesh export found.");
        return 4;
    }
    var exporter = new Exporter(mesh, new ExporterOptions());
    var success = exporter.TryWriteToDir(new DirectoryInfo(outputDirectory), out var label, out var savedFilePath);
    var result = new
    {
        schemaVersion = 1,
        requestedPath,
        packagePath,
        meshName = mesh.Name,
        success,
        label,
        savedFilePath,
        outputDirectory
    };
    Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
    return success ? 0 : 5;
}
catch (Exception error)
{
    Console.Error.WriteLine(JsonSerializer.Serialize(new
    {
        requestedPath,
        packagePath,
        error = error.Message,
        exception = error.GetType().FullName
    }, new JsonSerializerOptions { WriteIndented = true }));
    return 6;
}
