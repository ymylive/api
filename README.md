# API 代理服务集合

本仓库包含多个 AI API 代理服务的逆向实现。

## 项目结构

```
├── gemini-aistudio/    # Google AI Studio 逆向 API
├── gemini-cli/         # Gemini CLI 转 API
└── chatgpt/            # ChatGPT 逆向 API
```

## gemini-aistudio

将 Google AI Studio 网页版转换为 OpenAI 兼容 API。

- 支持流式输出
- 支持多模型切换
- 使用 Camoufox 反指纹浏览器

## gemini-cli

将 Gemini CLI 工具转换为 API 服务。

- OpenAI 兼容接口
- 原生 Gemini API 代理
- 支持 Google Search grounding

## chatgpt

ChatGPT 逆向 API 实现。

- OpenAI 兼容接口
- 支持 GPT-4 等模型

## 使用说明

请参考各子目录的 README 文件。

## 免责声明

本项目仅供学习研究使用，请遵守相关服务条款。
