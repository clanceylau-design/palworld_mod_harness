# 资产 Registry 合同

Phase 2 Registry 是从当前安装 Pak 索引派生的版本化资产目录。它只包含元数据和路径，不包含提取后的游戏资产。

每次扫描同时生成 `registry-audit.json`，列出未解析的模型、Skeleton、Physics Asset、纹理、本地化和玩法元数据覆盖情况。用该审计选择代表性测试目标，并安排后续深层解析优先级。

## 可信度模型

- `gameBuildId` 是 Registry 兼容性的权威版本键。
- `primarySkeletalMesh`、`skeleton` 和 `physicsAsset` 初始值来自包命名规则推断。
- `candidate` 只表示存在可能目标，不表示替换已通过运行验证。
- `gameplayMetadata: available` 表示已用 CUE4Parse 解析 build 匹配的 Pal 参数 DataTable，并按 tribe 名合并。
- `match: inherited-visual-variant` 表示皮肤或形态目录没有独立玩法行，引用去除后缀的基础 tribe；它不保证所有序列化模型依赖相同。
- `deepMetadata: available` 表示主 SkeletalMesh 已解析出材质槽、Skeleton/Physics Asset、骨骼层级和变换、Bounds 与 LOD 摘要。完整骨骼保存在 `mesh-metadata.json`，Registry 只保存紧凑投影。
- `textureDependencyGraph: available` 表示每个主模型材质槽的材质实例父链，以及有效纹理、标量、向量和静态开关参数已解析。`textureBindings` 将纹理分类为 `base_color`、`normal`、`packed_mros` 等角色。
- `deepMetadata: unavailable` 表示没有主模型候选，或负载反序列化失败。

## 刷新规则

Steam `buildid`、主 Pak 大小或 Pak 索引哈希改变时必须重新扫描。可以保留旧 Registry 做对比，但不得静默用于不同 build。

## 自然语言别名

扫描器直接从 build 匹配的 `DT_PalNameText_Common.uexp` 解析英文、简体中文和繁体中文 FString。只有需要补充额外名称时才传入 `--aliases aliases.json`；JSON 键为内部 Pal ID，值为字符串或字符串数组。不得在没有来源记录的情况下使用未版本化网络列表填充别名。
