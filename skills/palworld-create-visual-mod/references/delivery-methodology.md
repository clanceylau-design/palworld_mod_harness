# 自然语言到 Mod 的交付方法

## 阶段合同

1. 将需求转换为 `AssetSpec`，记录目标名称、外观变化、替换模式、硬约束和降级顺序。
2. 查询 build 匹配的 Registry，生成唯一 `TargetManifest`，包含模型、Skeleton、Physics Asset、材质和纹理的精确包路径及置信度。
3. 导出只读 `SourceBundle`，记录源哈希，绝不修改游戏安装。
4. 生成低成本概念参考。若需求改变几何体，概念图只用于确认方向，不是最终 UV 纹理。
5. 按替换模式分流。纯纹理任务使用原 UV；几何任务先完成 Blockout、骨架绑定、拓扑、材质槽和最终 UV。
6. 冻结 `ModelSurfaceContract`。尽量保持身体原 UV，把新附件放入独立非重叠图集；烘焙已有几何引导，并把不支持的通道记录为待办。
7. 生成并调理模型匹配候选，验证尺寸、通道、Alpha、UV、拓扑、变换、法线、材质槽、骨骼名、层级、权重、Bounds 与预算。
8. 绑定到目标，恢复精确 Unreal 路径与名称，复用已验证的原 Skeleton 和 Physics Asset。
9. 从不可变 UE 5.1.1 Pal 模板构建，导入、创建 PrimaryAssetLabel、Cook 指定 Chunk、审计 Pak 并创建 Loader 包。
10. 只安装到隔离测试环境，执行包、启动、日志、动画和视觉检查。
11. 只有 ValidationReport、预览证据、来源、版本和卸载说明全部通过时才交付 Mod。

## 必需中间产物

```text
AssetSpec
TargetManifest
SourceBundle
GeneratedCandidate
ModelSurfaceContract
ConditionedAsset
CookManifest
ModPackage
ValidationReport
```

每个产物都必须有 JSON Schema、内容哈希、生产者版本、游戏 build ID 和父产物哈希。每个阶段都应可独立恢复并保持幂等。

## 能力决策

- 不改变轮廓即可满足需求时，优先纯纹理。
- 只有原 Skeleton、骨骼层级、动画和目标比例保持有效时，才允许受限模型修改。
- 几何改变时必须执行“模型 → 骨架检查 → 拓扑/材质/UV 冻结 → 引导图 → 纹理”。
- 默认保持身体原 UV，并为附件使用独立图集；除非明确需要，不重排整张身体图集。
- 同骨架全模型替换在标准动画回归通过前保持实验状态。
- 第一版拒绝新骨架、新动画、任意物理和不兼容身体结构。

降级顺序：

```text
同骨架模型替换
→ 原模型受限修改
→ 原模型加已验证附件
→ 原模型加生成纹理
```
