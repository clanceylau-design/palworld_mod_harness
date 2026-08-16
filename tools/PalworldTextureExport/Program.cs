using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Versions;
using CUE4Parse.UE4.VirtualFileSystem;
using CUE4Parse_Conversion.Textures;

if (args.Length < 4)
{
    Console.Error.WriteLine("Usage: PalworldTextureExport <pak-directory> <mapping.usmap> <output-directory> <texture.uasset> [...]");
    return 2;
}

var pakDirectory = Path.GetFullPath(args[0]);
var mapping = Path.GetFullPath(args[1]);
var outputDirectory = Path.GetFullPath(args[2]);
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

static string SafeFileName(string value)
{
    foreach (var invalid in Path.GetInvalidFileNameChars()) value = value.Replace(invalid, '_');
    return value;
}

var exported = new List<object>();
var errors = new List<object>();
foreach (var requestedPath in args.Skip(3).Distinct(StringComparer.OrdinalIgnoreCase))
{
    var packagePath = NormalizePackagePath(requestedPath);
    try
    {
        var package = await provider.LoadPackageAsync(packagePath);
        var texture = package.GetExports().OfType<UTexture2D>().FirstOrDefault();
        if (texture is null)
        {
            errors.Add(new { requestedPath, packagePath, error = "No UTexture2D export found." });
            continue;
        }
        var decoded = texture.Decode();
        if (decoded is null)
        {
            errors.Add(new { requestedPath, packagePath, error = "Texture decoder returned null." });
            continue;
        }
        var bytes = decoded.Encode(ETextureFormat.Png, false, out var extension);
        var destination = Path.Combine(outputDirectory, SafeFileName(texture.Name) + "." + extension);
        await File.WriteAllBytesAsync(destination, bytes);
        exported.Add(new
        {
            requestedPath,
            packagePath,
            textureName = texture.Name,
            output = destination,
            width = texture.PlatformData.SizeX,
            height = texture.PlatformData.SizeY,
            pixelFormat = texture.Format.ToString(),
            compression = texture.CompressionSettings.ToString(),
            srgb = texture.SRGB,
            isNormalMap = texture.IsNormalMap
        });
    }
    catch (Exception error)
    {
        errors.Add(new { requestedPath, packagePath, error = error.Message, exception = error.GetType().FullName });
    }
}

var result = new { schemaVersion = 1, requested = args.Length - 3, exported = exported.Count, errors, textures = exported };
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
return errors.Count == 0 ? 0 : 5;
