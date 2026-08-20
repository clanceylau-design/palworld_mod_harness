# Tripo 原生 UV、V5 UV 与从零语义 UV 实验

> 日期：2026-08-20
> 目标：PinkCat / 呆猫替换候选
> 游戏 build：24575825

## 1. 结论

Tripo 贴图质量明显优于手工 V5，不是因为它追求绝对最低的 UV 拉伸，而是因为它保留少量连续图表、充分利用图集，并在三维表面完成多视图纹理融合。V5 的冻结代理 UV 来自一次全模型 `Smart UV Project`，25,000 个三角形被切成 1,404 个图表，图集三角形面积合计只有 6.59%，不适合让二维生成模型理解连续的脸、兜帽、手臂和衣摆。

本次从零实验确认了两点：

1. 将模型按语义区域分组后继续调用 `Smart UV Project` 仍然失败，不能解决碎岛问题。
2. 保留 Tripo 的图表拓扑作为教师，但完全丢弃其 UV 坐标，重新执行 Angle Based 参数化、语义密度调整、全局打包和贴图烘焙，可以得到通过静态门槛的新 UV。

这里的“从零”是指最终网格只保留新生成的 `SemanticUV`，UV 坐标哈希与 Tripo 原 UV 不同；它仍利用 Tripo 已经切开的连续表面作为图表教师。对于拓扑完全不同的重拓扑网格，还需要把教师图表/语义标签通过最近表面或重心坐标转移后再展开，不能直接复用本实验坐标。

## 2. 原始对比

| 指标 | V5 冻结代理 UV | Tripo 原生 UV |
|---|---:|---:|
| 三角形 | 25,000 | 1,470,812 |
| UV 图表 | 1,404 | 69 |
| 每图表三角形中位数 | 1 | 12,193 |
| 空间焊接后的 UV 拆分系数 | 1.249 | 1.020 |
| UV 三角形面积合计 | 6.59% | 65.10% |
| 方向拉伸 P50 | 1.184 | 1.077 |
| 方向拉伸 P90 | 1.434 | 1.428 |
| 方向拉伸 P99 | 1.621 | 2.561 |

V5 的局部拉伸和 texel density 一致性并不差，但这是以极端碎片化和约 93.4% 图集浪费换来的。两张 Base Color 均为 2048×2048；在没有重叠的前提下，Tripo 的表面像素面积约为 V5 的 9.9 倍。

## 3. 从零实验

输入为已通过 Blender 绑定门槛的 29,999 面 Tripo 候选：

```text
artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/
  deploy-rig-v4/BlueCat-TripoNative-PinkCatSkeleton.blend
```

### 3.1 失败方案：语义分区后 Smart Project

流程：

```text
PinkCat 骨骼主导区域 + 原贴图 cream/blue 分类
→ 每个语义区域分别 Smart Project，angle limit 89°
→ 全局平均密度、脸部 1.6×、重新打包
```

结果：

| 指标 | semantic-uv-v2 |
|---|---:|
| 图表 | 1,514 |
| 单三角图表 | 887 |
| 图集面积合计 | 16.82% |
| 方向拉伸 P90 | 2.101 |
| 状态 | `semantic_uv_quality_gate_failed` |

因此，“提高 Smart Project 角度”或“先分区再 Smart Project”都不能作为角色 UV 的稳定方案。

### 3.2 通过方案：教师图表 + 新参数化

流程：

```text
保留 Tripo 图表拓扑，不读取其最终坐标作为答案
→ 删除目标 UV，创建 SemanticUV
→ 对连续教师图表执行 Angle Based 参数化
→ 全局平均 texel density
→ 脸部 cream 区域密度提升 1.6×
→ 12 px 打包边距、16 px 烘焙扩边
→ 用临时只读 TripoSourceUV 将 Base Color 和 ORM 烘到 SemanticUV
→ 删除临时 TripoSourceUV
```

最终 `semantic-uv-v6`：

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| 最终 UV 层 | 仅 `SemanticUV` | 仅 1 层 |
| 图表 | 75 | ≤ 80 |
| 图表三角形中位数 | 319 | 记录值 |
| 继承的孤立单三角壳 | 3 | ≤ 3 |
| 图集三角形面积合计 | 55.486% | ≥ 55% |
| 退化 UV 三角形 | 0 | 0 |
| 方向拉伸 P50 / P90 / P99 | 1.019 / 1.092 / 2.180 | P90 ≤ 1.5 |
| 脸部/其他区域密度比 | 1.556× | ≥ 1.25× |
| Base Color 三角中心误差 P95 | 0.0124 | ≤ 0.08 |
| 误差大于 0.08 的三角形 | 1.10% | ≤ 1.5% |
| 几何哈希 | 前后一致 | 必须一致 |
| 权重哈希 | 前后一致 | 必须一致 |
| 状态 | `blender_static_semantic_uv_passed` | pass |

3 个单三角图表来自输入网格已经存在的孤立三角壳；在不焊接顶点、不改变几何哈希的约束下，UV 操作无法消除它们。本实验把它们作为显式受控例外，而不是隐藏或修改模型。

## 4. 可复现命令

必须使用配置中已验证的 Blender 4.2 LTS。示例：

```powershell
$blender = '<Blender 4.2 LTS blender.exe>'
$script = 'skills\palworld-create-visual-mod\scripts\blender_generate_semantic_uv.py'
$args = @(
  '--background'
  '--python'
  $script
  '--'
  '--input-blend'
  '<BlueCat-TripoNative-PinkCatSkeleton.blend>'
  '--mesh'
  'BlueCat_TripoNative_surface'
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

最终本地产物：

```text
artifacts/tripo-tests/build-24575825/PinkCat/blue-cat-mascot-v1/semantic-uv-v6/
  semantic-uv-report.json
  BlueCat-TripoNative-SemanticUVV1.blend
  T_BlueCat_BaseColor_SemanticUVV1.png
  T_BlueCat_ORM_SemanticUVV1.png
  semantic-uv-layout.png
  semantic-uv-rest-*.png
  semantic-uv-stress-*.png
```

这些生成物包含本地生成模型和绝对路径，不提交远程仓库；远程只保存脚本和结论。

## 5. 当前证据边界

已验证：

- UV 坐标从零生成，原坐标未进入最终网格；
- 几何和 PinkCat 权重哈希不变；
- Base Color、ORM 已烘到新 UV；
- 静止和合成压力姿势三视图未发现新 UV 裂缝或颜色爆炸；
- 脸、兜帽、身体和腿部的现有颜色边界在新 UV 上保持。

尚未验证：

- 原 `NormalGL` 不能按颜色直接搬运，因为更换 UV 会改变切线基；必须重新执行高模到低模的切线空间 Normal 烘焙；
- 合成压力姿势不能替代站立、行走、奔跑、攻击、受击等真实动画回归；
- 尚未把 SemanticUV 版本重新导出 PSK/FBX，也未在 UE 5.1.1 中导入、Cook 或在 Palworld 运行；
- 肩臂已有的变形折叠来自骨骼权重/拓扑，不是新 UV，但仍需后续动画质量修正。

## 6. 后续默认策略

```text
仅降面且原生贴图可用
→ 优先保留 Tripo 原 UV

需要重新参数化但几何仍保留 Tripo 图表拓扑
→ teacher_charts + fresh Angle Based + semantic density + rebake

真正重拓扑，顶点流与图表拓扑均改变
→ Tripo 高模语义/图表标签投射到低模
→ 隐藏区域与语义边界生成接缝
→ 新 UV
→ Base Color/ORM 烘焙 + 高低模切线 Normal 烘焙
```

不要再对整个角色或语义区域使用 Smart UV Project 作为最终 UV 方案。
