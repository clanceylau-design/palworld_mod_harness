# 工具链合同

## 支持基线

- 平台：Windows Steam 版 Palworld。
- 游戏身份：Steam App ID `1623730` 与 manifest 中的 `buildid`。
- 归档索引：repak；只用于确定性路径清单，不用于 UAsset 语义解析。
- 资产探索与导出：FModel，配置为 `GAME_UE5_1`，并使用 build 匹配的 Mapping。
- 玩法元数据：修补后的 PalworldDataExtractor，运行于 .NET 10，CUE4Parse 版本 `1.2.2.202608`；补丁和源码提交固定在锁定文件中。
- 模型编辑：Blender 4.2 LTS，PSK/PSA Add-on 安装在隔离的用户脚本目录。
- 纹理候选验证：Python 与 Pillow；`doctor.py` 验证导入成功后才能声明该阶段就绪。
- Cook 与打包：Unreal Engine 5.1.1；未经兼容验证不得替换为其他版本。

## 就绪等级

- `registryPathScan`：从 Pak 路径定位 Pal 目录并推断候选资产。
- `modelEditing`：在 Blender 中导入和操作 PSK/PSA。
- `gameplayMetadataExtraction`：反序列化 Pal 参数与本地化 DataTable，验证 Mapping/build 兼容性。
- `deepAssetMetadataPrerequisites`：FModel 与 build 匹配 `.usmap` 可用。
- `deepAssetMetadata`：解析主 SkeletalMesh 材质槽、骨架、LOD、Bounds、材质实例父链、参数和 Texture2D 元数据。
- `sourceBundleExport`：将目标分类纹理解码为 PNG，并生成只在本地保存的哈希清单。
- `textureCandidateValidation`：导入前验证 PNG 完整性、尺寸、通道、Alpha、角色约束和哈希。
- `skeletalMeshPreview`：在 Blender 中恢复原模型、UV、权重、材质槽和骨架并渲染多视图。
- `constrainedMeshEditing`：执行保留骨架的受限模型变形和刚性附件。
- `textureModPackaging`：注入 cooked Texture2D 并创建 V11 Pak。
- `unrealBuild`：使用 UE 5.1.1 重导入、Cook 和打包；当前因编辑器未配置而阻塞。
- `skeletalMeshModPackaging`：完成模型 Pak；当前未实现。

不得把路径推断视为权威依赖解析。只有负载反序列化或已验证导出确认后，才能把 `candidate` 链接升级为可信依赖。
