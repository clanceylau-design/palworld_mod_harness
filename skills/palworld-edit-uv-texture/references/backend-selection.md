# 纹理生成后端选择

## 第一版默认路径

在一张原始 `base_color` 图集上使用图像编辑模型，然后确定性恢复尺寸、源 Alpha 和源亮度结构。该路径不要求本地扩散环境，适合换色、表面图案和克制的风格化；它不能单独证明 3D 模型上的接缝正确性。

## 3D 感知升级路径

只有在能够导出带原 UV 的 OBJ/MTL，并具备自动多角度预览后，才考虑同步多视图生成：

- SyncMVD 通过重叠 UV 区域共享去噪内容，减少接缝和碎片。官方实现使用 MIT 许可，主要面向 Linux/NVIDIA，Windows 建议用 WSL，需要 PyTorch3D 和干净的 OBJ/MTL/纹理输入。应避免翻转法线、重叠 UV 和约 4 万三角面以上模型。来源：https://github.com/LIU-Yuxin/SyncMVD
- TEXTure 通过多视图渲染、深度引导、投影和修补迭代生成纹理，也支持初始纹理细化和局部涂鸦编辑。其固定依赖较旧并依赖 Kaolin，只作为研究后端。来源：https://github.com/TEXTurePaper/TEXTurePaper
- Paint3D 增加 UV 修补和高清细化，用于消除孔洞与烘焙光照。官方环境面向 CentOS、PyTorch 1.12.1、CUDA 11.6 和 Kaolin。来源：https://github.com/OpenTexture/Paint3D
- TEXGen 直接在 UV 空间扩散 Albedo，但推理建议使用 24 GB 显存，并以 Linux/Docker 为主。12 GB 显存机器不得默认选择。来源：https://github.com/CVMI-Lab/TEXGen
- MVPaint 组合同步多视图、3D 感知修补、UV 超分辨率和接缝平滑。模型导出和预览合同稳定后再评估。来源：https://mvpaint.github.io/

## 不可放宽的生产规则

- 第一版原 UV 流程只生成 Albedo/Base Color；Normal、打包材质图、Mask 和参数绑定保持不变。
- 保持当前游戏 build 的精确纹理名、尺寸、Alpha 行为和包绑定。
- 生成结果不得包含烘焙光照，否则会在游戏动态光照下产生错误。
- 宣称视觉或接缝质量前必须完成多角度渲染。Blender 烘焙和纹理过滤需要 UV 岛边距，避免 Mip 接缝渗色。来源：https://docs.blender.org/manual/en/latest/render/cycles/baking.html
