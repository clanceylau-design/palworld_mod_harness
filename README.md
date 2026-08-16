# Palworld AI 视觉 Mod 工具链

本项目验证一条本地、可审计、可恢复的 Palworld 视觉 Mod 生产线：将自然语言需求解析为目标资产合同，检索游戏资源，生成或修改模型与纹理，在 Blender 中验证，最后通过 Unreal Engine 5.1.1 重导入、烘焙并打包为 Mod。

## 当前结论

- 当前适配 Steam build：`24575825`。
- 资产 Registry 已扫描 304 个 Pal 模型目录，其中 296 个完成 SkeletalMesh、材质、纹理、骨架、Physics Asset、LOD 与 Bounds 深层解析。
- 已验证原 UV 纹理生成、确定性调理、原骨架 Blender 预览、UE 5.1 Texture2D 注入和 V11 Pak 打包。
- 已以皮皮鸡（`ChickenPal`）验证“先模型、后 UV、再纹理”的机械装甲链路：受限模型修改、附件独立 UV 图集、PBR 纹理、四视图预览、最终 PSK 导出—回导验证均已通过。
- 已生成 Unreal 模型导入 Bundle；当前唯一硬阻塞是本机尚未安装或配置 **Unreal Editor 5.1.1**。
- 纹理 Pak 和 SkeletalMesh Mod 均尚未完成游戏内运行回归，不能标记为最终交付。

## 必读文档

- [当前进度与断点续作手册](docs/current-status-and-handoff.zh-CN.md)：新会话或新机器从这里开始。
- [产品目标、调研结论与实施路线](docs/product-research-and-roadmap.zh-CN.md)：产品边界和长期计划。
- [Palworld 视觉 Mod Skill](skills/palworld-create-visual-mod/SKILL.md)：确定性执行流程。
- [原 UV 纹理编辑 Skill](skills/palworld-edit-uv-texture/SKILL.md)：纹理专用流程。

## 快速检查

先复制并填写本机工具链配置：

```powershell
Copy-Item -LiteralPath config\toolchain.example.json -Destination config\toolchain.local.json
python skills\palworld-create-visual-mod\scripts\doctor.py --config config\toolchain.local.json
```

`doctor.py` 的 `block` 表示对应阶段必须停止。当前工作只有在 `unreal-5.1.1` 变为 `pass` 后，才能进入 SkeletalMesh 重导入、Cook 和 Pak。

## 仓库与本地产物边界

仓库只提交可复现的源代码、Skill、Schema、锁定文件和中文文档。以下内容只保留在本机，不进入远程仓库：

- 从游戏 Pak 提取的资产；
- 生成的纹理、PSK、Blend、Pak 和预览图；
- 带绝对路径的 `config/toolchain.local.json`；
- 编译输出和下载的第三方二进制工具。

这样可以避免分发游戏资产、泄漏机器路径，并让新环境按照锁定配置重新生成产物。
