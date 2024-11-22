from transformers import M2M100Tokenizer

# 加载分词器
tokenizer = M2M100Tokenizer.from_pretrained("facebook/m2m100_418M")

# 要统计的文本
text = "Hello, how are you?"

# 使用分词器编码文本
tokens = tokenizer.encode(text)

# 输出 token 数量
print("Number of tokens:", len(tokens))
