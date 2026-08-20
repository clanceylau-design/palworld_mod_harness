---
name: palworld-create-visual-mod
description: 检查并配置本地 Palworld 视觉 Mod 工具链，查询与游戏 build 匹配的 Pal 资产，生成纯纹理变体，执行保留骨架的受限模型修改，定稿附件 UV 图集，在 Blender 中预览，并打包已支持的纹理 Mod。需要从自然语言定位 Pal、扫描游戏资产或构建本地模型/纹理替换流程时使用。
---

# Palworld 视觉 Mod

只处理用户本人拥有的本地 Palworld 安装。不得分发从游戏中提取的资产。除非用户明确要求安装，否则所有生成物都保留在游戏目录之外。

## 先读取交接状态

开始工作前阅读仓库中的 `docs/current-status-and-handoff.zh-CN.md`。新会话必须先运行 `git status --short` 和下述 Doctor，不得仅凭旧对话推断状态。

## 验证环境

运行：

```powershell
python scripts/doctor.py --config ../../../config/toolchain.local.json
```

将 `block` 视为对应阶段的硬停止条件。Doctor 报告路径不明确或版本不匹配时，不得猜测工具位置。配置或修复依赖时阅读 [工具链合同](references/toolchain.md)。

在新 Windows 机器上，可用以下脚本安装锁定的非 Unreal 工具：

```powershell
pwsh -NoLogo -NoProfile -File scripts/bootstrap_tools.ps1 -ToolRoot <绝对路径>
```

脚本必须按 [toolchain.lock.json](references/toolchain.lock.json) 校验下载归档。

## 建立 Pal 资产 Registry

游戏 build 改变后，先提取与 build 匹配的玩法和本地化表：

```powershell
python scripts/extract_pal_metadata.py --config ../../../config/toolchain.local.json --output ../../../artifacts/deep-metadata
```

该命令同时承担 Mapping 语义验证。只有核心 Pal 参数表、本地化表成功解析，且 Steam build 与当前安装一致时，生成的 `.usmap` 才可用。

随后扫描 Pak 并合并玩法元数据：

```powershell
python scripts/scan_game_assets.py --config ../../../config/toolchain.local.json --output ../../../artifacts/asset-registry --gameplay-metadata ../../../artifacts/deep-metadata/build-<buildid>/palworld-data-extractor/data.json
```

最后解析主 SkeletalMesh 并补全 Registry：

```powershell
python scripts/extract_mesh_metadata.py --config ../../../config/toolchain.local.json --registry ../../../artifacts/asset-registry/build-<buildid>/pal-assets.json --output ../../../artifacts/deep-metadata --enriched-registry ../../../artifacts/asset-registry/build-<buildid>/pal-assets.json
```

这一步记录材质槽、材质实例父链与有效参数、纹理元数据、Skeleton、Physics Asset、骨骼层级和变换、Bounds、LOD、Morph Target、Socket 与顶点色信息，但不输出可编辑模型。

## 解析目标并导出 SourceBundle

先精确查询目标：

```powershell
python scripts/query_pal_registry.py --registry <pal-assets.json> --query <名称或内部代号>
```

必须得到唯一匹配，不能在多个变体之间猜测。然后用 `prepare_target_manifest.py` 将用户原始需求编译为 `AssetSpec` 与 `TargetManifest`。仅在需求明确时使用 `--mode auto`；否则显式选择 `texture_only`、`constrained_mesh` 或 `same_skeleton_mesh`。

导出目标的只读纹理绑定：

```powershell
python scripts/export_source_bundle.py --config ../../../config/toolchain.local.json --registry ../../../artifacts/asset-registry/build-<buildid>/pal-assets.json --query <精确名称> --output ../../../artifacts/source-bundles
```

SourceBundle 包含游戏解码资产，只能本地保存。Steam build 改变后不得复用旧 Registry。修改扫描规则前阅读 [资产 Registry 合同](references/asset-registry.md)。

## 按需求选择执行路径

- `texture_only`：在原 UV 上生成并验证纹理，再绑定原 SkeletalMesh 预览。
- 仅模型变形且 UV 不变：先定稿和验证模型；只有 UV 哈希保持一致时才复用原纹理合同。
- 新增拓扑或附件：先完成模型、骨架绑定、材质槽和 UV；身体保留原 UV，附件使用独立非重叠图集；随后才生成模型匹配纹理。

改变几何体时，模型定稿前生成的纹理只能标记为 `concept_reference`。

## 执行已验证的纯纹理流程

要求 `GeneratedCandidate` 和 `TargetManifest` 均已通过验证，然后运行：

```powershell
python scripts/preview_texture_candidate.py --config ../../../config/toolchain.local.json --target-manifest <target-manifest.json> --candidate <candidate-root> --output <pipeline-output>
```

该入口核对 Steam build 和纹理门禁，导出原模型 PSK，在 Blender 中恢复原拓扑、UV、材质槽、Skeleton 与权重，渲染四视图，保存 `.blend` 并写入报告。所有嵌套报告必须为 `status: pass`。

Base Color 生成和确定性调理使用同级 `palworld-edit-uv-texture` Skill。

## 执行模型优先的受限 3D 流程

用 `ConstrainedMeshEditSpec` 描述修改。已支持：

- `scale_weighted_region`：缩放权重区域；
- `offset_weighted_region`：平移权重区域；
- `sharpen_weighted_tip`：锐化羽毛、耳朵或头冠末端；
- `add_bone_armor_plate`：添加绑定既有骨骼的小型刚性装甲。

运行：

```powershell
python scripts/customize_skeletal_mesh.py --config ../../../config/toolchain.local.json --target-manifest <target-manifest.json> --candidate <candidate-root> --spec <mesh-edit-spec.json> --output <mesh-output>
```

必须保持原身体拓扑、UV、权重、骨架层级和材质槽；位移必须受限；附件必须绑定已存在骨骼并带 Armature modifier。此阶段输出只是 Blockout 与 Blender 预览，不是可部署 Mod。

定稿轮廓和附件后冻结表面合同：

```powershell
python scripts/finalize_attachment_uv.py --config ../../../config/toolchain.local.json --blend <customized.blend> --edit-report <mesh-edit-report.json> --output <surface-output> --atlas-size 1024
```

`model-surface-contract.json` 必须为 `pass`，并证明身体 UV 未改变、附件图集单元唯一、几何状态为 `frozen_for_texture_generation`。

生成附件 Base Color 后执行确定性回写：

```powershell
python scripts/condition_attachment_atlas.py --surface-contract <model-surface-contract.json> --raw-image <generated-atlas.png> --output <candidate-output>
```

要求尺寸、RGB 模式、图集单元和 UV 外黑色像素全部通过。候选必须引用冻结表面合同的哈希。

生成 PBR 贴图：

```powershell
python scripts/bake_attachment_pbr.py --config ../../../config/toolchain.local.json --surface-contract <model-surface-contract.json> --base-color <armor-base-color.png> --output <pbr-output>
```

当前输出 AO、切线空间 Normal、曲率近似、MRAO 和橙色 Emissive Mask。MRAO 合同为 `R=Metallic`、`G=Roughness`、`B=AO`。没有高模时，Normal 只是最终低模自烘焙，曲率只是 Pointiness 近似，不能宣称为高低模细节烘焙。

用 `blender_preview_model_surface.py` 绑定身体、眼睛、附件 Base Color、Normal、MRAO 和 Emissive，要求所有绑定检查及四个渲染输出通过。

UV 和材质定稿后重新导出最终 PSK：

```powershell
python scripts/export_finalized_skeletal_mesh.py --config ../../../config/toolchain.local.json --surface-contract <model-surface-contract.json> --output <psk-output>
```

必须验证顶点、面、骨骼、材质集合、UV 及每个附件图集单元的 PSK 导出—回导一致性。

最后用 `prepare_unreal_model_import.py` 组装 Unreal 导入 Bundle。只有配置的编辑器精确为 UE 5.1.1 时，清单才可进入导入阶段。

不得用此流程创建功能性新肢体、多骨骼变形附件、新骨架、新动画或任意生成模型。刚性附件仍需 Unreal 重导入、材质绑定、动画与 Physics Asset 回归。

## 实验性 Tripo 教师图表语义 UV

当同骨架替换候选已经完成降面、绑定和几何冻结，但需要丢弃 Tripo 原 UV 坐标并重新参数化时，可运行：

```powershell
$blender = '<Blender 4.2 LTS blender.exe>'
$args = @(
  '--background'
  '--python'
  'scripts\blender_generate_semantic_uv.py'
  '--'
  '--input-blend'
  '<已绑定且保留 Tripo UV 的 Blend>'
  '--mesh'
  '<目标网格对象名>'
  '--output-dir'
  '<本地输出目录>'
  '--atlas-size'
  '2048'
  '--unwrap-strategy'
  'teacher_charts'
  '--face-density-scale'
  '1.6'
  '--pack-margin-pixels'
  '12'
)
& $blender @args
```

该实验入口：

- 以输入网格已有的 Tripo 连续图表拓扑作为教师，但不复用原 UV 坐标；
- 按 PinkCat 骨骼主导区域和原 Base Color 的 cream/blue 大类记录语义；
- 对教师图表重新执行 Angle Based 参数化，提高脸部 texel density，再全局打包；
- 通过临时只读 `TripoSourceUV` 把 Base Color 和 ORM 烘到新 `SemanticUV`，随后删除临时 UV；
- 验证 UV 哈希改变、几何和权重哈希不变、图表数量、图集利用率、拉伸、脸部密度、纹理转移误差，以及静止/合成压力三视图。

不得把 `semantic_smart` 当作最终方案；实测即使按语义分区并把 Smart Project 角度提高到 89°，仍会生成大量碎岛。对拓扑完全不同的重拓扑网格，`teacher_charts` 也不能直接适用，必须先把教师图表/语义标签投射到低模，再生成新接缝。

切线空间 Normal 不能像 Base Color 或 ORM 一样按颜色搬运。UV 改变后必须重新做高模到低模的切线 Normal 烘焙。在该步骤、最终 PSK/FBX 往返、真实动画以及 UE 5.1.1 运行回归完成前，输出只能标记为 Blender 实验候选。完整证据见 `docs/tripo-uv-regeneration-study.zh-CN.md`。

## 打包已支持的输出

纯纹理候选通过预览后运行：

```powershell
python scripts/package_texture_mod.py --config ../../../config/toolchain.local.json --target-manifest <target-manifest.json> --candidate <candidate-root> --output <package-output> --mod-name <安全名称>
```

打包器只提取原 cooked Texture2D，确认 UE 5.1 合同，注入候选并保持格式、尺寸和 Mip 数，回读 PNG 审计压缩误差，再按原路径创建 V11 Zlib Pak。它不会安装或启动游戏。交付前必须另做运行验证。

SkeletalMesh 打包仍依赖 UE 5.1.1 重导入和 Cook。不得把 PSK 直接放入 Pak，也不得宣称纹理注入流程支持模型。

## 保持确定性边界

- 用脚本完成发现、验证、Registry、资产调理、Unreal 导入、Cook 和打包。
- 用 Agent 完成意图理解、目标选择、策略决策与错误说明。
- 保持精确 Unreal 包路径和资产名。
- 除非未来流程明确支持，否则保持原 Skeleton、骨骼层级、动画和 Physics Asset。
- 所有产物记录工具版本、游戏 build ID、输入/输出哈希和验证状态。
- Blender 预览不等于 Unreal 或游戏内验证。

当前已实现 Registry、SourceBundle、原 UV 候选、受限模型修改、附件 UV、模型匹配 PBR、Tripo 教师图表语义 UV 实验、Blender 预览、最终 PSK 往返验证、Unreal 导入 Bundle、Texture2D 注入和纹理 Pak。尚未实现 SemanticUV 高低模切线 Normal 烘焙、Palworld 兼容的 UE 5.1.1 SkeletalMesh Cook、模型 Pak 安装和运行回归。

规划后续阶段时阅读 [自然语言到 Mod 的交付方法](references/delivery-methodology.md)。方法文档是合同，不代表执行器已经实现。
