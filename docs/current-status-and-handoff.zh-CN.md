# 当前进度与断点续作手册

> 更新时间：2026-08-20
>
> 当前工作分支：`main`
>
> 当前适配游戏 build：`24575825`
>
> 本文用途：让没有历史对话的新会话或新机器准确恢复当前工作。

## 1. 项目目标与首版边界

首版目标是提供一套本地 Agent Skill，使用户能够用自然语言指定任意 Pal，并完成纹理替换或保留原骨架的受限模型定制，最终输出可安装 Mod。

首版必须保持：

- 不新增、删除、改名或重排骨骼；
- 不替换动画蓝图，不生成新动画；
- 原 Skeleton、动画和 Physics Asset 可复用；
- 修改模型时先冻结几何、材质槽和 UV，再生成最终纹理；
- 所有阶段输出带 build ID、父产物哈希和验证状态的报告；
- 游戏提取资产与机器专属路径只保留在本机。

不在首版范围：新骨架、复杂多骨骼附件、任意身体结构、自动新动画、未经回归的公开发布。

## 2. 当前实际进度

| 能力 | 状态 | 已验证事实 |
|---|---|---|
| 工具链诊断 | 已完成 | `doctor.py` 能按阶段报告通过项与阻塞项 |
| build 匹配 Mapping | 已完成 | UE4SS 生成的 `.usmap` 已通过 Pal 参数表和本地化表语义解析 |
| Pal 资产 Registry | 已完成 | 304 个模型目录；296 个完成深层元数据；2144 条纹理绑定 |
| 自然语言目标编译 | 已完成 | 可生成 `AssetSpec` 与 `TargetManifest` |
| SourceBundle | 已完成 | 可按目标导出分类纹理和哈希清单 |
| 原 UV 纹理生成 | 已完成 | Base Color 生成、尺寸/Alpha/结构调理与验证通过 |
| 原模型/骨架预览 | 已完成 | ChickenPal 的 PSK、UV、权重、骨架和材质可在 Blender 四视图验证 |
| 纹理 Mod 打包 | 已完成但未运行回归 | 已生成 UE 5.1 cooked Texture2D 注入的 V11 Pak |
| 受限模型修改 | 已完成 | 翅膀/头冠锐化与 4 个刚性骨骼附件通过 Blender 验证 |
| 模型优先 UV | 已完成 | 身体保持原 UV；4 个附件使用 1024²、2×2 非重叠图集 |
| 模型匹配 PBR | 已完成 | Base Color、AO、低模自烘焙 Normal、曲率近似、MRAO、Emissive Mask |
| 最终 PSK | 已完成 | 5074 顶点、8652 面、39 骨骼/Socket、3 材质槽往返一致 |
| Unreal 导入 Bundle | 已完成 | 路径、Skeleton、Physics Asset、材质槽、压缩和通道合同已固定 |
| Tripo 呆猫原生贴图模型 | 已完成候选 | 原生 UV/PBR 保留；29,999 面；已绑定 PinkCat 43 骨骼/Socket，Blender 压力测试通过 |
| Tripo 从零语义 UV | Blender 实验通过 | 最终仅 `SemanticUV`；75 个图表；55.49% 图集占用；几何/权重哈希不变；Base Color/ORM 回烘及静止/合成压力三视图通过 |
| UE 5.8 实验导入/Cook/Pak | 已完成但不兼容 | UE 5.8.1 导入和 Cook 均通过，V11 Pak 路径审计通过；UE5.1 解析器确认 Cooked SkeletalMesh 不兼容 |
| Palworld 兼容 SkeletalMesh Cook | 阻塞 | 仍需 Unreal Editor 5.1.1 重新 Cook；UE 5.8 不能作为其替代版本 |
| 游戏内动画/物理回归 | 未开始 | 依赖 Cook 后的可加载模型 Pak |

## 3. 当前可信产物

以下路径是当前工作站上的本地产物，不会提交到 Git。继续工作前应先确认文件存在且报告状态一致。

### 3.1 Registry 与源数据

- `artifacts/asset-registry/build-24575825/pal-assets.json`
- `artifacts/asset-registry/build-24575825/registry-audit.json`
- `artifacts/deep-metadata/build-24575825/mesh-metadata.json`
- `artifacts/source-bundles/build-24575825/ChickenPal/source-bundle.json`

### 3.2 纹理 Mod 验证

- Pak：`artifacts/mod-packages/build-24575825/ChickenPal/mechanical-v1/ChickenPal_Mechanical_P.pak`
- 报告：`artifacts/mod-packages/build-24575825/ChickenPal/mechanical-v1/mod-package-report.json`
- 当前状态：报告 `pass`，但尚未安装和运行验证。

### 3.3 模型优先机械皮皮鸡

- 表面合同：`artifacts/model-surface-contracts/build-24575825/ChickenPal/mechanical-armor-v2/model-surface-contract.json`
- PBR 报告：`artifacts/generated-candidates/build-24575825/ChickenPal/mechanical-armor-v2-model-first/pbr/attachment-pbr-report.json`
- PBR 预览：`artifacts/mesh-customizations/build-24575825/ChickenPal/mechanical-armor-v2-model-first-pbr-preview/model-first-preview-report.json`
- 最终 PSK：`artifacts/mesh-customizations/build-24575825/ChickenPal/mechanical-armor-v2-finalized-psk/SK_ChickenPal_Mechanical_Final.psk`
- PSK 报告：`artifacts/mesh-customizations/build-24575825/ChickenPal/mechanical-armor-v2-finalized-psk/finalized-psk-report.json`
- Unreal Bundle：`artifacts/unreal-import-bundles/build-24575825/ChickenPal/mechanical-armor-v2/unreal-model-import-manifest.json`

报告状态应分别为：

```text
ModelSurfaceContract: pass
AttachmentPbrTextureSet: pass
ModelFirstTexturedPreviewReport: pass
FinalizedSkeletalMeshInterchangeReport: pass
UnrealModelImportManifest: blocked_editor_missing
```

### 3.4 Tripo 呆猫替换 PinkCat（2026-08-20）

- Tripo 原生模型：`artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/bailian-h3.1-standard-textured/blue-cat-bailian-tripo-h3.1-standard-textured-pbr.glb`
- Blender 绑定报告：`artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/deploy-rig-v4/tripo-native-rig-report.json`
- Tripo/V5 UV 量化目录：`artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/uv-comparison/`
- 从零语义 UV 报告：`artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/semantic-uv-v6/semantic-uv-report.json`
- 从零语义 UV Blend：`artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/semantic-uv-v6/BlueCat-TripoNative-SemanticUVV1.blend`
- UE 5.8 实验项目：`artifacts/unreal-experimental/build-24575825/PinkCat/BlueCatMod58/BlueCatMod58.uproject`
- UE 导入报告：`artifacts/unreal-experimental/build-24575825/PinkCat/BlueCatMod58/Saved/bluecat-import-report.json`
- 实验 Pak：`artifacts/mod-packages/build-24575825/PinkCat/blue-cat-tripo-ue58-experimental-v1/BlueCat_PinkCat_UE58_Experimental_P.pak`
- 总报告：`artifacts/mod-packages/build-24575825/PinkCat/blue-cat-tripo-ue58-experimental-v1/deployment-report.json`
- UE5.1 兼容探针：`artifacts/mod-packages/build-24575825/PinkCat/blue-cat-tripo-ue58-experimental-v1/ue51-compatibility-probe.json`

当前证据边界：Blender 绑定、UE 5.8 导入、UE 5.8 定向 Cook 和 Pak 结构已通过；UE5.1 解析器对该 Pak 返回 `Invalid FString length`，所以没有安装到游戏，也不能标记为运行时可用。

从零语义 UV 的可复现脚本为 `skills/palworld-create-visual-mod/scripts/blender_generate_semantic_uv.py`，完整方法和失败对照见 `docs/tripo-uv-regeneration-study.zh-CN.md`。当前 `NormalGL` 未搬运，因为 UV 改变后原切线空间法线失效；必须重新做高模到低模的切线 Normal 烘焙。

## 4. 已确认的关键经验

### 4.1 任务必须先分流

```text
纯纹理需求
→ 原 UV 纹理生成 → 2D 验证 → 原模型预览 → Texture Pak

仅变形且 UV 不变
→ 模型定稿 → 骨架/拓扑/UV 哈希验证 → 纹理生成 → 预览

新增附件或拓扑
→ 模型 Blockout → 骨架绑定 → 几何定稿
→ 身体保留原 UV + 附件独立图集
→ 几何引导图/PBR → 模型匹配纹理 → 预览
→ UE 5.1.1 重导入/Cook → Pak → 游戏内回归
```

模型修改任务中，在几何定稿前生成的纹理只能标记为 `concept_reference`，不能标记为最终纹理。

### 4.2 当前 PBR 的真实边界

- AO 来自最终低模的 Cycles 烘焙。
- Normal 是最终低模的切线空间自烘焙，基本为中性法线；没有高模时不能宣称具有高频几何细节。
- Curvature 是 Cycles Geometry Pointiness 近似，不是高低模曲率烘焙。
- MRAO 合同为：R=Metallic、G=Roughness、B=Ambient Occlusion。
- Emissive Mask 来自机械装甲 Base Color 中的橙色能量区域。
- 这些通道必须由未来 Unreal 材质按同一合同读取。

### 4.3 Tripo UV 与重新展开

- V5 的 25,000 面代理 UV 来自全模型 Smart UV Project，共 1,404 个图表，图集三角形面积合计只有 6.59%；碎岛是二维纹理难以理解语义边界的主要原因之一。
- Tripo 原生高模约 69 个大图表，图集三角形面积合计 65.10%。其贴图优势还来自多视图、几何与纹理联合生成，不能只归因于 UV。
- 将模型按语义区域分组后继续 Smart UV Project 仍产生 1,514 个图表，已实测否定。
- 当前通过方案只学习 Tripo 的连续图表拓扑，丢弃其坐标，重新 Angle Based 参数化；随后提升脸部密度、全局打包，并把 Base Color/ORM 烘到新 UV。
- 对拓扑完全不同的重拓扑网格，只能投射教师图表/语义标签并重新生成接缝，不能直接使用 Tripo UV 坐标。

## 5. 当前唯一硬阻塞

`config/toolchain.local.json` 已识别 UE 5.8.1 并允许实验使用，因此：

- `doctor.py` 的 `unreal-editor` 为 `pass`，`unrealBuild=true`；
- `palworld-cook-compatibility` 为 `warn`，`palworldCompatibleUnrealCook=false`；
- UE 5.8 已实际完成 SkeletalMesh 导入、材质绑定、定向 Cook 和 Pak；
- UE5.1 构建匹配解析器已证明 UE 5.8 Cooked SkeletalMesh 不能被当前 Palworld 资产格式读取。

必须安装精确版本 Unreal Editor 5.1.1，并配置：

```json
"unrealEditor": "D:\\Epic Games\\UE_5.1\\Engine\\Binaries\\Win64\\UnrealEditor.exe"
```

同时读取 `Engine/Build/Build.version`，确认 `MajorVersion=5`、`MinorVersion=1`、`PatchVersion=1`。不得用其他版本代替。

## 6. 同一工作站的下一步

1. 为 SemanticUV 版本执行 Tripo 高模到 29,999 面低模的切线空间 Normal 重新烘焙；不得把原 `NormalGL` 当普通颜色图搬运。
2. 重新导出 SemanticUV 版本 PSK/FBX，并复核 UV、几何、权重和骨骼往返一致。
3. 在 Epic Games Launcher 安装 UE 5.1.1，并配置其路径；保留 UE 5.8 作为实验导入/诊断工具。
4. 运行：

```powershell
python skills\palworld-create-visual-mod\scripts\doctor.py --config config\toolchain.local.json
```

5. 要求 `unreal-editor`、`palworld-cook-compatibility` 和 `unrealBuild` 全部通过。
6. 重新运行 `prepare_unreal_model_import.py`，要求清单状态从 `blocked_editor_missing` 变为 `ready_for_unreal_import`。
7. 复制当前 `BlueCatMod58` 实验项目为不可变 UE 5.1.1 Pal 模板，并复用自动导入 Worker。
8. 导入最终 PSK 与 PBR 纹理，复用原 Skeleton 和 Physics Asset，严格保持目标包路径与资产名。
9. 验证目标对应的材质槽、纹理通道、Skeleton 和 Physics Asset。
10. Cook 指定 Chunk，审计 Pak 路径。
11. 安装到隔离测试环境，执行启动、日志、动画、穿模和 Physics Asset 回归。

## 7. 全新机器的恢复流程

### 7.1 克隆与配置

```powershell
git clone https://github.com/clanceylau-design/palworld_mod_harness.git
Set-Location palworld_mod_harness
Copy-Item -LiteralPath config\toolchain.example.json -Destination config\toolchain.local.json
```

填写本机 Palworld、Steam manifest、工具目录和 UE 5.1.1 路径。不要提交 `toolchain.local.json`。

### 7.2 安装非 Unreal 工具

```powershell
pwsh -NoLogo -NoProfile -File skills\palworld-create-visual-mod\scripts\bootstrap_tools.ps1 `
  -ToolRoot D:\PalworldModHarnessTools
```

随后配置 Blender PSK/PSA Add-on，并通过 UE4SS 在目标游戏 build 上生成 `.usmap`。Mapping 只有在参数表和本地化表可解析后才算有效。

### 7.3 重建本地产物

按顺序运行：

```text
doctor.py
→ extract_pal_metadata.py
→ scan_game_assets.py
→ extract_mesh_metadata.py
→ prepare_target_manifest.py
→ export_source_bundle.py
```

若要重现机械皮皮鸡，则继续执行模型定制、表面合同、附件纹理、PBR、预览、最终 PSK 与 Unreal Bundle 脚本。完整命令和阶段门禁见 `skills/palworld-create-visual-mod/SKILL.md`。

由于仓库不分发游戏资产，新机器必须拥有用户自己的 Palworld 安装并重新提取本地产物。

## 8. 新会话启动指令

没有历史上下文的 Agent 应按以下顺序工作：

1. 阅读本文件、`README.md` 和两个 Skill。
2. 运行 `git status --short`，不得覆盖用户未提交的修改。
3. 运行 `doctor.py`，以实际状态而非旧对话为准。
4. 检查第 3 节关键报告及其父哈希。
5. 若 UE 5.1.1 仍缺失，只能准备导入合同，不能宣称模型 Mod 已完成。
6. 若 UE 5.1.1 已通过，从第 6 节第 4 步继续。

可直接使用以下任务描述：

```text
阅读 docs/current-status-and-handoff.zh-CN.md，运行 doctor.py 并复核关键报告。
保持 build 24575825、原 Skeleton、Physics Asset、包路径和材质槽合同。
若 UE 5.1.1 已配置，从 Unreal 导入 Bundle 开始实现 SkeletalMesh 重导入、Cook、Pak 和隔离运行回归。
不得把 Blender 预览或未运行验证的 Pak 标记为最终交付。
```

## 9. 完成定义

只有同时满足以下条件，机械皮皮鸡模型 Mod 才能标记为完成：

- UE 5.1.1 精确版本验证通过；
- SkeletalMesh 导入成功并复用原 Skeleton、Physics Asset；
- 材质槽和 PBR 通道绑定正确；
- Cook 和 Pak 路径审计通过；
- 隔离游戏实例成功加载；
- 站立、行走、奔跑、攻击、受击等动作无顶点爆炸或严重穿模；
- 日志无 Missing Asset、T-Pose、NaN 等错误；
- 有游戏内截图或视频证据；
- 交付报告包含卸载方法和兼容 build。

## 10. 中文 Skill 的 Windows 校验

部分中文 Windows Python 环境会让 `quick_validate.py` 默认使用 GBK 读取 UTF-8 Skill，导致 `UnicodeDecodeError`。这不是 Skill 格式错误。验证前显式启用 UTF-8：

```powershell
$env:PYTHONUTF8 = '1'
python C:\Users\<用户名>\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\palworld-create-visual-mod
python C:\Users\<用户名>\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\palworld-edit-uv-texture
```
