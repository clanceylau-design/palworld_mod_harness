---
name: palworld-edit-uv-texture
description: 将自然语言 Palworld 外观需求和既有 SourceBundle 转换为保持原 UV 布局的 Base Color 候选，把图像模型输出确定性回写到精确源合同，并在 Unreal 导入前完成验证。需要在保留模型、UV、材质槽、法线、打包材质图和 Alpha 的前提下生成、换色或重绘 Pal 原纹理时使用。
---

# Palworld 原 UV 纹理编辑

游戏提取资产和生成候选必须保留在本机。不得覆盖 SourceBundle 或游戏安装文件。

## 准备单一受限编辑任务

只有纯纹理任务，或父流程已经冻结模型并证明 UV 合同未改变时，才可直接使用原 UV 流程。若出现新拓扑或附件，不得把它们绘制到原身体图集；应先使用父 Skill 建立附件专用的 `ModelSurfaceContract`。

用 `scripts/prepare_generation_job.py` 传入 SourceBundle、TargetManifest、用户未改写的原始需求和材质槽选择器。默认从 `body` 开始；只有需求明确涉及眼睛或嘴部时才编辑对应纹理。脚本必须只选择一个 `base_color` 绑定，并生成带哈希的 `TextureGenerationJob`。

第一版不得生成 Normal、MROS、Mask、Subsurface 或 Emissive；这些通道保持源文件不变。

## 生成图像提案

读取任务中的 `promptSpec`。调用图像生成或编辑工具前，先检查被选中的本地源 PNG。必须把源图视为编辑目标，而不是普通风格参考。

要求图像模型保持精确 UV 图集布局、UV 岛边界、特征位置、全画布覆盖和局部明暗结构，只改变用户要求的表面颜色与图案。拒绝 UV 岛移动、新增物体、文字、边框、场景背景或烘焙方向光。

原始生成图永远不是交付物。若几何修改任务在模型定稿前生成图像，应标记为 `concept_reference`；即使尺寸和 Alpha 检查通过，也不能标记为模型匹配或最终纹理。

## 确定性调理

用 `scripts/condition_candidate.py` 传入生成任务和原始生成图。脚本恢复源尺寸和 Alpha，保留源亮度结构，按原文件名写入编辑纹理，逐字节复制其他 SourceBundle 纹理，并输出带哈希的 `GeneratedCandidate`。

首轮使用默认结构保持强度。只有用户明确要求更强绘制细节，且后续已有 3D 预览时，才可降低该强度。

## 交接前验证

使用父 Skill 的 `validate_texture_candidate.py` 验证候选目录。必须同时满足：

- 调理器报告 `structureStatus: pass`；
- 父级 `ValidationReport` 报告 `status: pass`。

这只证明 2D 资产合同通过，不证明在 3D 模型上的视觉质量。通过后，使用父 Skill 的 PSK 导出与 `blender_render_skeletal_preview.py`。在 `preview-report.json` 为 `pass` 之前，候选保持待定状态。

安装本地扩散后端或宣称 3D 感知接缝一致性前，阅读 [后端选择](references/backend-selection.md)。与后续阶段集成时阅读 [产物合同](references/artifact-contracts.md)。
