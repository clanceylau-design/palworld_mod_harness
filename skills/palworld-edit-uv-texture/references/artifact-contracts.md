# 产物合同

`TextureGenerationJob` 将一个自然语言需求绑定到 SourceBundle 中一个精确的 `base_color` 文件。它记录源文件和父产物哈希、提示词不变量、输出尺寸、绑定关系和默认调理参数。

`GeneratedCandidate` 包含一个按原文件名保存的调理后编辑结果，以及 SourceBundle 其余纹理的逐字节相同副本。它记录原始生成来源、候选哈希、源亮度相关性、Alpha 精确状态和 `previewStatus`。

只有 `structureStatus` 和父级纹理 `ValidationReport` 同时通过时，候选才能进入下一阶段。`previewStatus: pending` 表示 2D 合同已通过，但尚未绑定模型渲染。

对于改变几何体的任务，上述通过状态只验证原表面合同。父流程生成冻结的 `ModelSurfaceContract` 前，必须记录 `usageClassification: concept_reference`。模型匹配候选必须引用该表面合同，并为每个新增材质图集提供独立输出。
