# Embedding 模型目录

这个目录用于存放本项目本地运行的文本向量化模型。

## 当前用途

长期记忆检索会把 `agent_memory/` 中的成功经验转换成 memory chunks，再用这里的 embedding 模型编码成向量，写入本地 FAISS 向量库：

```text
agent_memory/vector_store/xhs_agent_worklog.faiss
agent_memory/vector_store/xhs_agent_worklog_metadata.json
agent_memory/vector_store/xhs_exploration_memory.faiss
agent_memory/vector_store/xhs_exploration_memory_metadata.json
```

注意：

- `embedding/` 存放模型文件。
- `agent_memory/vector_store/*.faiss` 存放 FAISS 向量索引。
- `agent_memory/vector_store/*_metadata.json` 存放向量编号到原始长期记忆的映射。
- 两者不是同一个东西。

## 推荐目录结构

```text
embedding/
  README.md
  bge-small-zh-v1.5/
    config.json
    modules.json
    tokenizer.json
    model.safetensors
    ...
```

当前 `cfg/memory.yaml` 默认读取：

```yaml
memory:
  retrieval:
    embedding:
      provider: local_model
      local_model:
        model_name: embedding/bge-small-zh-v1.5
        cache_dir: embedding
```

如果你更换模型，只需要修改 `model_name`。

## 可放置的模型

推荐优先级：

1. `BAAI/bge-small-zh-v1.5`
   - 轻量，中文检索够用，适合当前长期记忆规模。

2. `BAAI/bge-base-zh-v1.5`
   - 比 small 更强，但更慢、更占内存。

3. `BAAI/bge-m3`
   - 多语言、长文本能力更强，适合后续大规模 RAG。

4. `shibing624/text2vec-base-chinese`
   - 中文语义匹配模型，可作为对照测试。

## 构建向量库

安装依赖：

```powershell
D:\ANACONDA\envs\xhs_agent\python.exe -m pip install -U sentence-transformers
```

构建或更新 FAISS 向量库：

```powershell
D:\ANACONDA\envs\xhs_agent\python.exe test\integration\build_memory_embedding_index.py
```

测试检索效果：

```powershell
D:\ANACONDA\envs\xhs_agent\python.exe test\integration\test_interactive_memory_retrieval.py
```

## 维护建议

- 模型文件通常很大，不建议提交到 Git。
- 保留本 README，模型目录本身可以按需下载或复制。
- 如果替换模型，建议重新构建向量库，因为不同模型生成的向量不可混用。
- 如果只新增或修改了少量记忆，向量库会根据 `text_hash` 只编码变化的 chunk。
