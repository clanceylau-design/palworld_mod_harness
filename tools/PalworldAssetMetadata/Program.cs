using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Vfs;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse.UE4.VirtualFileSystem;

if (args.Length != 3)
{
    Console.Error.WriteLine("Usage: PalworldAssetMetadata <pak-directory> <mapping.usmap> <skeletal-mesh.uasset|pal-assets.json>");
    return 2;
}

var pakDirectory = Path.GetFullPath(args[0]);
var mapping = Path.GetFullPath(args[1]);
var input = Path.GetFullPath(args[2]);
var requests = new List<(string PalId, string AssetPath)>();
var missingPrimaryMesh = new List<string>();

if (input.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
{
    using var registry = JsonDocument.Parse(File.ReadAllText(input));
    foreach (var pal in registry.RootElement.GetProperty("pals").EnumerateArray())
    {
        var palId = pal.GetProperty("palId").GetString() ?? "unknown";
        if (pal.TryGetProperty("primarySkeletalMesh", out var meshElement) && meshElement.ValueKind == JsonValueKind.String)
            requests.Add((palId, meshElement.GetString()!));
        else
            missingPrimaryMesh.Add(palId);
    }
}
else
{
    requests.Add((Path.GetFileNameWithoutExtension(input), args[2]));
}

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

static string NormalizeAssetPath(string value)
{
    var normalized = value.Replace('\\', '/').TrimStart('/');
    return normalized.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ? normalized[..^7] : normalized;
}

static object? PackageReference(FPackageIndex? index)
{
    if (index is null || index.IsNull) return null;
    return new { index = index.Index, name = index.Name, path = index.ResolvedObject?.GetPathName() };
}

static object? ResolvedReference(ResolvedObject? value)
{
    if (value is null) return null;
    return new { name = value.Name.Text, path = value.GetPathName() };
}

static object? RegisterMaterial(ResolvedObject? value, Dictionary<string, ResolvedObject> materials)
{
    if (value is null) return null;
    materials.TryAdd(value.GetPathName(), value);
    return ResolvedReference(value);
}

static object Vector(FVector value) => new { x = value.X, y = value.Y, z = value.Z };

static object Color(FLinearColor value) => new { r = value.R, g = value.G, b = value.B, a = value.A };

static object ObjectReference(UObject value) => new
{
    name = value.Name,
    path = value.GetPathName(),
    type = value.ExportType
};

static object TextureMetadata(UTexture texture) => new
{
    name = texture.Name,
    path = texture.GetPathName(),
    type = texture.ExportType,
    width = texture.PlatformData.SizeX,
    height = texture.PlatformData.SizeY,
    mipCount = texture.PlatformData.Mips?.Length ?? 0,
    pixelFormat = texture.Format.ToString(),
    compression = texture.CompressionSettings.ToString(),
    srgb = texture.SRGB,
    isNormalMap = texture.IsNormalMap,
    lodGroup = texture.LODGroup.ToString(),
    filter = texture.Filter.ToString(),
    addressX = texture.GetTextureAddressX().ToString(),
    addressY = texture.GetTextureAddressY().ToString()
};

static object MaterialMetadata(UUnrealMaterial material, Dictionary<string, object> textures)
{
    var chain = new List<UUnrealMaterial>();
    var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    UUnrealMaterial? current = material;
    while (current is not null && seen.Add(current.GetPathName()) && chain.Count < 32)
    {
        chain.Add(current);
        current = current is UMaterialInstance instance ? instance.Parent : null;
    }

    var effectiveTextures = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
    var effectiveScalars = new Dictionary<string, float>(StringComparer.OrdinalIgnoreCase);
    var effectiveVectors = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
    var effectiveSwitches = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
    foreach (var layer in chain.AsEnumerable().Reverse().OfType<UMaterialInstanceConstant>())
    {
        foreach (var parameter in layer.TextureParameterValues)
        {
            var reference = PackageReference(parameter.ParameterValue);
            effectiveTextures[parameter.Name] = new { name = parameter.Name, texture = reference };
            if (parameter.ParameterValue.TryLoad<UTexture>(out var texture))
                textures[texture.GetPathName()] = TextureMetadata(texture);
        }
        foreach (var parameter in layer.ScalarParameterValues)
            effectiveScalars[parameter.Name] = parameter.ParameterValue;
        foreach (var parameter in layer.VectorParameterValues)
            if (parameter.ParameterValue is { } color)
                effectiveVectors[parameter.Name] = Color(color);
        if (layer.StaticParameters is not null)
            foreach (var parameter in layer.StaticParameters.StaticSwitchParameters)
                effectiveSwitches[parameter.Name] = parameter.Value;
    }

    var referenced = new List<UUnrealMaterial>();
    material.AppendReferencedTextures(referenced, false);
    foreach (var texture in referenced.OfType<UTexture>())
        textures[texture.GetPathName()] = TextureMetadata(texture);

    var top = material as UMaterialInstanceConstant;
    return new
    {
        name = material.Name,
        path = material.GetPathName(),
        type = material.ExportType,
        parentChain = chain.Skip(1).Select(ObjectReference).ToArray(),
        localTextureParameters = top?.TextureParameterValues.Select(parameter => new
        {
            name = parameter.Name,
            texture = PackageReference(parameter.ParameterValue)
        }).ToArray() ?? [],
        localScalarParameters = top?.ScalarParameterValues.Select(parameter => new
        {
            name = parameter.Name,
            value = parameter.ParameterValue
        }).ToArray() ?? [],
        localVectorParameters = top?.VectorParameterValues.Where(parameter => parameter.ParameterValue.HasValue).Select(parameter => new
        {
            name = parameter.Name,
            value = Color(parameter.ParameterValue!.Value)
        }).ToArray() ?? [],
        effectiveTextureParameters = effectiveTextures.Values.ToArray(),
        effectiveScalarParameters = effectiveScalars.Select(pair => new { name = pair.Key, value = pair.Value }).ToArray(),
        effectiveVectorParameters = effectiveVectors.Select(pair => new { name = pair.Key, value = pair.Value }).ToArray(),
        effectiveStaticSwitches = effectiveSwitches.Select(pair => new { name = pair.Key, value = pair.Value }).ToArray(),
        referencedTextures = referenced.OfType<UTexture>().Select(ObjectReference).ToArray()
    };
}

static object MeshMetadata(string palId, string assetPath, USkeletalMesh mesh, Dictionary<string, ResolvedObject> materialReferences)
{
    var bones = mesh.ReferenceSkeleton.FinalRefBoneInfo.Select((bone, index) =>
    {
        var pose = mesh.ReferenceSkeleton.FinalRefBonePose[index];
        return new
        {
            index,
            name = bone.Name.Text,
            parentIndex = bone.ParentIndex,
            transform = new
            {
                translation = Vector(pose.Translation),
                rotation = new { x = pose.Rotation.X, y = pose.Rotation.Y, z = pose.Rotation.Z, w = pose.Rotation.W },
                scale = Vector(pose.Scale3D)
            }
        };
    }).ToArray();

    return new
    {
        palId,
        assetPath,
        exportName = mesh.Name,
        exportType = mesh.ExportType,
        skeleton = PackageReference(mesh.Skeleton),
        physicsAsset = PackageReference(mesh.PhysicsAsset),
        bounds = new
        {
            origin = Vector(mesh.ImportedBounds.Origin),
            boxExtent = Vector(mesh.ImportedBounds.BoxExtent),
            sphereRadius = mesh.ImportedBounds.SphereRadius
        },
        materialSlots = mesh.SkeletalMaterials.Select((material, index) => new
        {
            index,
            slotName = material.MaterialSlotName.Text,
            importedSlotName = material.ImportedMaterialSlotName?.Text,
            material = RegisterMaterial(material.Material, materialReferences)
        }).ToArray(),
        boneCount = bones.Length,
        bones,
        lodCount = mesh.LODModels?.Length ?? mesh.LODInfo.Length,
        lods = mesh.LODModels?.Select((lod, index) => new
        {
            index,
            vertices = lod.NumVertices,
            sections = lod.Sections.Length,
            textureCoordinates = lod.NumTexCoords,
            activeBones = lod.ActiveBoneIndices?.Length ?? 0
        }).ToArray(),
        morphTargetCount = mesh.MorphTargets.Length,
        socketCount = mesh.Sockets.Length,
        hasVertexColors = mesh.bHasVertexColors,
        vertexColorChannels = mesh.NumVertexColorChannels
    };
}

var assets = new List<object>();
var errors = new List<object>();
var materialReferences = new Dictionary<string, ResolvedObject>(StringComparer.OrdinalIgnoreCase);
foreach (var request in requests)
{
    var assetPath = NormalizeAssetPath(request.AssetPath);
    try
    {
        var package = await provider.LoadPackageAsync(assetPath);
        var mesh = package.GetExports().OfType<USkeletalMesh>().FirstOrDefault();
        if (mesh is null)
            errors.Add(new { palId = request.PalId, assetPath, error = "No USkeletalMesh export found." });
        else
            assets.Add(MeshMetadata(request.PalId, assetPath, mesh, materialReferences));
    }
    catch (Exception error)
    {
        errors.Add(new { palId = request.PalId, assetPath, error = error.Message, exception = error.GetType().FullName });
    }
}

var materials = new List<object>();
var materialErrors = new List<object>();
var textures = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
foreach (var reference in materialReferences.Values)
{
    try
    {
        var material = reference.Load<UUnrealMaterial>();
        if (material is null)
            materialErrors.Add(new { path = reference.GetPathName(), error = "Material reference did not load as UUnrealMaterial." });
        else
            materials.Add(MaterialMetadata(material, textures));
    }
    catch (Exception error)
    {
        materialErrors.Add(new { path = reference.GetPathName(), error = error.Message, exception = error.GetType().FullName });
    }
}

var result = new
{
    schemaVersion = 1,
    mapping,
    requested = requests.Count,
    extracted = assets.Count,
    missingPrimaryMesh,
    errors,
    materialCount = materials.Count,
    materialErrors,
    materials,
    textureCount = textures.Count,
    textures = textures.Values.ToArray(),
    assets
};
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
return errors.Count == 0 && materialErrors.Count == 0 ? 0 : 5;
