import os
import re
from typing import List, Union
import tiktoken
from icecream import ic
from .client_llm import client_qwen
import asyncio
from asyncio import Semaphore
import functools
import logging
import uuid
import json
import traceback

# 设置每个文本块的最大标记数量，如果文本超过这个标记数量，我们将把它分成多个块
TOKENS_PER_CHUNK = 800

# 配置日志记录，记录错误信息到 'error_log.txt' 文件中
logging.basicConfig(filename='error_log.txt', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')


def log_error_as_json(unique_id, prompt, system_message, result_format, error):
    """
    将错误信息保存为 JSON 格式的日志。

    参数:
        unique_id (str): 错误的唯一ID。
        prompt (str): 提示词的内容。
        system_message (str): 系统消息内容。
        result_format (str): 结果的期望格式。
        error (Exception): 捕获到的异常对象。
    """
    error_data = {
        "Error ID": unique_id,
        "Prompt": prompt,
        "System message": system_message,
        "Result format": result_format,
        "Error": str(error)
    }
    # 将错误信息保存为 JSON 格式
    with open('error_log.json', 'a', encoding='utf-8') as f:
        json.dump(error_data, f, ensure_ascii=False)
        f.write('\n')  # 确保每条日志占一行


def log_detailed_error(unique_id, prompt, system_message, result_format, error, retries):
    """
    保存更详细的错误日志，包括重试次数等额外信息。

    参数:
        unique_id (str): 错误的唯一ID。
        prompt (str): 提示词的内容。
        system_message (str): 系统消息内容。
        result_format (str): 结果的期望格式。
        error (Exception): 捕获到的异常对象。
        retries (int): 当前错误发生时的重试次数。
    """
    error_data = {
        "Error ID": unique_id,
        "Prompt": prompt,
        "System message": system_message,
        "Result format": result_format,
        "Error": str(error),
        "Retries": retries
    }
    with open('detailed_error_log.json', 'a', encoding='utf-8') as f:
        json.dump(error_data, f, ensure_ascii=False, indent=4)
        f.write('\n')  # 分隔不同的错误日志


# 定义一个异步函数，用于与大型语言模型进行交互，获取文本的处理结果
async def get_completion(prompt: str, system_message: str = "You are a helpful assistant.",
                         model: str = "qwen2-72b-instruct", result_format: str = 'message',
                         max_retries: int = 5) -> Union[str, dict]:
    """
    与大型语言模型进行交互以获取处理结果。包括处理错误、重试机制等。

    参数:
        prompt (str): 发送给语言模型的提示词。
        system_message (str): 系统消息，用于定义模型的角色。
        model (str): 使用的模型名称，默认为 "qwen2-72b-instruct"。
        result_format (str): 期望的返回结果格式，默认为 'message'。
        max_retries (int): 最大重试次数，默认为 5。

    返回值:
        Union[str, dict]: 返回的结果，可能是字符串或字典格式。
    """
    loop = asyncio.get_running_loop()  # 获取当前运行的事件循环
    retries = 0  # 初始化重试次数
    unique_id = None  # 预先定义 unique_id 变量

    while retries < max_retries:
        try:
            # 使用 functools.partial 将函数 client_qwen 和参数打包，以便在 run_in_executor 中执行
            result = await loop.run_in_executor(None, functools.partial(client_qwen, system_message, prompt,
                                                                        result_format=result_format))

            if result is None:
                raise ValueError("client_qwen 返回 None")  # 如果结果为空，则抛出异常

            response, token_count = result  # 解包结果

            if result_format == 'message':
                # 检查 response 是否为字符串，如果是，尝试解析为 JSON
                if isinstance(response, str):
                    try:
                        response = json.loads(response)  # 尝试将字符串解析为 JSON
                    except json.JSONDecodeError:
                        print(f"解析失败的结果为：{type(response)}\n{response}")
                        print(f"解析失败: {traceback.format_exc()}")
                        pass  # 如果解析失败，保持 response 为字符串

            if isinstance(response, dict) and 'translate' in response and result_format == 'message':
                # 如果 response 是一个字典并包含 'translate' 字段
                return response['translate']
            elif result_format == 'text':
                # 如果 result_format 是 'txt'，直接返回 response
                return response
            else:
                print(f"LLM 格式解析错误：{traceback.format_exc()}")
                return response

        except Exception as e:
            unique_id = str(uuid.uuid4())  # 生成唯一的错误ID
            log_error_as_json(unique_id, prompt, system_message, result_format, e)  # 保存为 JSON 日志
            logging.error(f"Error ID: {unique_id} - Error: {str(e)}")  # 同时保存为 TXT 日志
            print(traceback.format_exc())
            retries += 1  # 增加重试次数
            await asyncio.sleep(5)  # 等待 5 秒后重试

    if unique_id is None:
        unique_id = str(uuid.uuid4())  # 在没有异常的情况下，也生成一个 unique_id

    return f"\n\n未成功获取LLM结果 - 错误ID: {unique_id}\n\n"  # 返回错误信息给用户


# 定义一个异步函数，用于翻译文本块
async def one_chunk_translation(source_lang: str, target_lang: str, country: str, source_text: str,
                                result_format: str) -> str:
    """
    使用大型语言模型将文本块进行翻译。

    参数:
        source_lang (str): 源语言的代码。
        target_lang (str): 目标语言的代码。
        source_text (str): 要翻译的文本块。
        country (str): 国家信息，用于翻译的风格匹配。

    返回值:
        str: 翻译后的文本块。
    """
    # 设置系统消息，指定模型的角色为翻译专家
    system_message = f"您是一名翻译专家，专门从事从 {source_lang} 到 {target_lang} 的翻译。最终的翻译风格和语气应与在 {country} 日常口语中的 {target_lang} 风格相匹配。"

    # 构建翻译提示词，要求将文本从源语言翻译为目标语言
    if result_format == 'message':
        translation_prompt = f"""
        以下文本是Markdown格式的文档内容，请将其从 {source_lang} 翻译为 {target_lang}：
        
        注意：
        1. 请严格保留原文的Markdown结构，确保所有的占位符（<PH></PH>）及其包裹的内容不被翻译或修改。对于图片描述，请确保其与图片链接分开处理。
        2. 请确保严格保留原文中markdown结构的标识，不要新增或者删除markdown标识以带来困惑。
        3. 遇到作者姓名和参考文献时，请保持原文，不进行翻译。
        4. 专有名词保留：原文中的专有名词（如人名、地名、品牌名等）应保持不变，避免误译。
            4.1 不需要翻译的专有名词举例如(不区分大小写)：AI Agent、transformer、LLM、dspy、LangChain等。
        5. 请仅翻译文本内容，避免对占位符和其他非文本内容进行任何修改。
        6. 请以标准的 UTF-8 编码返回结果，避免使用 Unicode 转义字符。
        
        原文:
        {source_text}
        
        请按下文json格式要求输出，不要有其他非json内容，并确保Markdown格式未被改变：
        {{
        "attention": 简要说明原文翻译的难点,
        "translate": 翻译后的文本
        }}
        """
    else:
        translation_prompt = f"""
        以下文本是Markdown格式的文档内容，请将其从 {source_lang} 翻译为 {target_lang}：

        注意：
        1. 请严格保留原文的Markdown结构，确保所有的占位符（<PH></PH>）及其包裹的内容不被翻译或修改。对于图片描述，请确保其与图片链接分开处理。
        2. 请确保严格保留原文中markdown结构的标识，不要新增或者删除markdown标识以带来困惑。
        3. 遇到作者姓名和参考文献时，请保持原文，不进行翻译。
        4. 专有名词保留：原文中的专有名词（如人名、地名、品牌名等）应保持不变，避免误译。
            4.1 不需要翻译的专有名词举例如(不区分大小写)：AI Agent、transformer、LLM、dspy、LangChain等。
        5. 请仅翻译文本内容，避免对占位符和其他非文本内容进行任何修改。
        6. 请以标准的 UTF-8 编码返回结果，避免使用 Unicode 转义字符。

        原文:
        {source_text}

        请仅输出翻译后的文本内容，保留原始占位符和markdown结构标识，不要添加额外说明或解释。
        """

    # 调用 get_completion 函数获取翻译结果
    translation = await get_completion(translation_prompt, system_message=system_message, result_format=result_format)
    return translation  # 返回翻译后的文本


# 定义一个异步函数，用于改进翻译文本块
async def one_chunk_improve_translation(
        source_lang: str,
        target_lang: str,
        country: str,
        original_text: str,
        translated_text: str,
        result_format: str

) -> str:
    """
    使用 LLM 对翻译进行改进检查并优化。

    参数:
        source_lang (str): 源语言的代码。
        target_lang (str): 目标语言的代码。
        original_text (str): 原始文本块。
        translated_text (str): 翻译后的文本块。
        country (str): 翻译相关的国家信息。

    返回值:
        str: 改进后的翻译文本块。
    """
    # 设置系统消息，指定模型的角色为翻译质量审查员
    system_message = f"您是一名翻译质量审查员，专门从事从 {source_lang} 到 {target_lang} 的翻译质量改进。请确保翻译风格和语气符合在 {country} 日常口语中的 {target_lang} 风格。"

    # 构建改进提示词，要求模型根据提供的翻译文本进行改进
    if result_format == 'message':
        improvement_prompt = f"""
        这是一个针对Markdown格式文档内容的翻译质量改进请求，翻译方向是从 {source_lang} 到 {target_lang}。请根据以下标准对提供的译文进行分析、批评，并基于这些批评和建议改进翻译：
        
        注意：
        1. 请严格保留原文的Markdown结构，确保所有的占位符（<PH></PH>）及其包裹的内容不被翻译或修改。对于图片描述，请确保其与图片链接分开处理。
        2. 请确保严格保留原文中markdown结构的标识，不要新增或者删除markdown标识以带来困惑。
        3. 遇到作者姓名和参考文献时，请保持原文，不进行翻译。
        4. 专有名词保留：原文中的专有名词（如人名、地名、品牌名等）应保持不变，不要翻译。
        4.1 不需要翻译的专有名词举例如(不区分大小写)：AI Agent、transformer、LLM、dspy、LangChain等。
        5. 请仅对需要改进的文本内容进行调整，避免对已经正确的部分（如Markdown结构、占位符等）进行修改。
        6. 请以标准的 UTF-8 编码返回结果，避免使用 Unicode 转义字符。
        
        源文本和初次翻译如下，以 XML 标签 <SOURCE_TEXT></SOURCE_TEXT> 和 <TRANSLATION></TRANSLATION> 分隔：
        
        <SOURCE_TEXT>
        {original_text}
        </SOURCE_TEXT>
        
        <TRANSLATION>
        {translated_text}
        </TRANSLATION>
        
        请在编写建议和改进时，特别注意以下方面：
        (i) 准确性：通过纠正添加、误译、遗漏或未翻译的错误，确保翻译准确反映源文本的内容。
        (ii) 流畅性：确保译文符合 {target_lang} 的语法、拼写和标点符号规则，避免不必要的重复。
        (iii) 风格：确保翻译反映源文本的风格，并考虑 {country} 的文化背景。
        (iv) 术语：确保术语使用的一致性，避免上下文不合适或不一致的使用。
        (v) 其他错误：修正其他可能存在的翻译错误。
        
        输出要求：
        1. 提供具体、有帮助的建议清单。
        2. 直接输出改进后的译文，不要输出其他非必要内容。
        3. 改进后的译文应符合上述所有要求，保留原始 Markdown 结构和所有占位符。
        
        请按下文json格式要求输出，不要有其他非json内容，并确保Markdown格式未被改变：
        {{
        "attention": 你认为翻译质量可以改进的方面,
        "translate": 改进后的译文
        }}
        """
    else:
        improvement_prompt = f"""
        这是一个针对Markdown格式文档内容的翻译质量改进请求，翻译方向是从 {source_lang} 到 {target_lang}。请根据以下标准对提供的译文进行分析、批评，并基于这些批评和建议改进翻译：

        注意：
        1. 请严格保留原文的Markdown结构，确保所有的占位符（<PH></PH>）及其包裹的内容不被翻译或修改。对于图片描述，请确保其与图片链接分开处理。
        2. 请确保严格保留原文中markdown结构的标识，不要新增或者删除markdown标识以带来困惑。
        3. 遇到作者姓名和参考文献时，请保持原文，不进行翻译。
        4. 专有名词保留：原文中的专有名词（如人名、地名、品牌名等）应保持不变，不要翻译。
        4.1 不需要翻译的专有名词举例如(不区分大小写)：AI Agent、transformer、LLM、dspy、LangChain等。
        5. 请仅对需要改进的文本内容进行调整，避免对已经正确的部分（如Markdown结构、占位符等）进行修改。
        6. 请以标准的 UTF-8 编码返回结果，避免使用 Unicode 转义字符。

        源文本和初次翻译如下，以 XML 标签 <SOURCE_TEXT></SOURCE_TEXT> 和 <TRANSLATION></TRANSLATION> 分隔：

        <SOURCE_TEXT>
        {original_text}
        </SOURCE_TEXT>

        <TRANSLATION>
        {translated_text}
        </TRANSLATION>

        请在编写建议和改进时，特别注意以下方面：
        (i) 准确性：通过纠正添加、误译、遗漏或未翻译的错误，确保翻译准确反映源文本的内容。
        (ii) 流畅性：确保译文符合 {target_lang} 的语法、拼写和标点符号规则，避免不必要的重复。
        (iii) 风格：确保翻译反映源文本的风格，并考虑 {country} 的文化背景。
        (iv) 术语：确保术语使用的一致性，避免上下文不合适或不一致的使用。
        (v) 其他错误：修正其他可能存在的翻译错误。

        请仅输出改进后的译文文本内容，保留原始占位符和markdown结构标识，不要添加额外说明或解释。
        """

    # 调用 get_completion 函数获取改进后的翻译结果
    improved_translation = await get_completion(improvement_prompt, system_message=system_message,
                                                result_format=result_format)
    return improved_translation  # 返回改进后的翻译文本


# 计算输入字符串中的标记数量
def num_tokens_in_string(input_str: str, encoding_name: str = "cl100k_base") -> int:
    """
    计算给定字符串中的标记数量。

    参数:
        input_str (str): 输入字符串。
        encoding_name (str): 使用的编码名称，默认为 "cl100k_base"。

    返回值:
        int: 字符串中的标记数量。
    """
    encoding = tiktoken.get_encoding(encoding_name)  # 获取指定编码
    num_tokens = len(encoding.encode(input_str))  # 编码输入字符串并计算标记数量
    return num_tokens  # 返回标记数量


# 计算分块大小，确保文本块不超过指定的标记限制
def calculate_chunk_size(token_count: int, token_limit: int) -> int:
    """
    根据给定的标记总数和限制，计算合适的分块大小。

    参数:
        token_count (int): 文本的标记总数。
        token_limit (int): 每个块的最大标记数量。

    返回值:
        int: 计算出的块大小。
    """
    if token_count <= token_limit:
        return token_count  # 如果标记数量小于等于限制，返回原始标记数量

    num_chunks = (token_count + token_limit - 1) // token_limit  # 计算需要的块数
    chunk_size = token_count // num_chunks  # 计算每个块的大小

    remaining_tokens = token_count % token_limit  # 计算剩余的标记数量
    if remaining_tokens > 0:
        chunk_size += remaining_tokens // num_chunks  # 将剩余标记分配给各个块

    return chunk_size  # 返回计算后的块大小


# 根据最大标记数拆分文本
# 进行优先级分割
def split_text(text: str, max_tokens: int, token_flexibility: float = 0.5) -> List[str]:
    """
    根据最大标记数拆分文本，支持按标题、段落和句子结束符优先级分割。

    参数:
        text (str): 要拆分的文本。
        max_tokens (int): 每个块的最大标记数。
        token_flexibility (float): 标记数的灵活性范围，默认为 0.5。

    返回值:
        List[str]: 拆分后的文本块列表。
    """
    min_tokens = max_tokens * (1 - token_flexibility)
    max_tokens_flex = max_tokens * (1 + token_flexibility)

    # 优先级 1: 标题分割
    title_split_pattern = re.compile(r'(\n#+\s)')
    # 优先级 2: 段落分割
    paragraph_split_pattern = re.compile(r'(\n{2,})')
    # 优先级 3: 句子结束符分割
    sentence_split_pattern = re.compile(r'([\.\!\?]\s)')

    chunks = []
    current_chunk = ""

    def try_split(pattern, chunk):
        """
        尝试按照给定的模式分割文本块。

        参数:
            pattern (Pattern): 正则表达式模式。
            chunk (str): 当前要分割的文本块。

        返回值:
            str: 分割后剩余的未处理文本。
        """
        parts = re.split(pattern, chunk)
        new_chunk = ""
        for part in parts:
            temp_chunk = new_chunk + part
            token_count = num_tokens_in_string(temp_chunk)

            if min_tokens <= token_count <= max_tokens_flex:
                chunks.append(temp_chunk.strip())
                new_chunk = ""
            else:
                new_chunk += part
        return new_chunk

    # 尝试按照标题分割
    current_chunk = try_split(title_split_pattern, text)

    # 如果剩余内容仍然存在，尝试按照段落分割
    if current_chunk:
        current_chunk = try_split(paragraph_split_pattern, current_chunk)

    # 如果仍然有剩余内容，尝试按照句子结束符分割
    if current_chunk:
        current_chunk = try_split(sentence_split_pattern, current_chunk)

    # 添加剩余的部分
    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


# 在信号量的控制下运行异步任务
async def run_with_semaphore(semaphore, coro):
    """
    在信号量的控制下运行异步任务，以限制并发数。

    参数:
        semaphore (Semaphore): 用于限制并发的信号量对象。
        coro (Coroutine): 要执行的协程。

    返回值:
        Any: 协程的返回结果。
    """
    async with semaphore:
        return await coro  # 在信号量限制内运行协程


# 定义一个异步函数，用于翻译Markdown文本
async def translate_markdown(
        source_lang: str,
        target_lang: str,
        country: str,
        source_text: str,
        max_tokens: int = TOKENS_PER_CHUNK,
        semaphore_limit: int = 5,
        result_format: str = 'message'
) -> str:
    """
    使用大型语言模型将Markdown格式的文本进行分块翻译和优化。

    参数:
        source_lang (str): 源语言的代码。
        target_lang (str): 目标语言的代码。
        country (str): 翻译相关的国家信息。
        source_text (str): 要翻译的Markdown格式文本。
        max_tokens (int): 每个块的最大标记数量，默认为 800。
        semaphore_limit (int): 最大并发任务数，默认为 5。

    返回值:
        str: 翻译后的Markdown格式文本。
    """
    semaphore = Semaphore(semaphore_limit)  # 创建一个信号量，用于限制并发任务的数量
    num_tokens_in_text = num_tokens_in_string(source_text)  # 计算文本中的标记数量
    ic("文本的 token 数:", num_tokens_in_text)

    failed_chunks = []

    async def process_chunk(chunk, index, retries=0) -> str:
        """
        处理单个文本块，包括翻译和改进。支持错误处理和重试机制。

        参数:
            chunk (str): 要处理的文本块。
            index (int): 文本块的索引位置。
            retries (int): 当前处理时的重试次数，默认为 0。

        返回值:
            str: 处理后的文本块结果。
        """
        try:
            initial_translation = await run_with_semaphore(
                semaphore, one_chunk_translation(source_lang, target_lang, country, chunk, result_format=result_format)
            )
            improved_translation = await run_with_semaphore(
                semaphore,
                one_chunk_improve_translation(source_lang, target_lang, country, chunk, initial_translation,
                                              result_format=result_format)
            )
            return improved_translation
        except Exception as e:
            print(traceback.format_exc())
            if retries >= 5:
                unique_id = str(uuid.uuid4())
                log_detailed_error(unique_id, chunk, "", "", e, retries)
                failed_chunks.append((index, chunk))  # 保留失败的块及其索引
                return f"\n\n未成功获取LLM结果 - 错误ID: {unique_id}\n\n"
            else:
                return await process_chunk(chunk, index, retries + 1)

    if num_tokens_in_text < max_tokens:
        ic("文本将在单个块中翻译")
        return await process_chunk(source_text, 0)
    else:
        ic("文本将被分割并翻译")
        source_text_chunks = split_text(source_text, max_tokens)
        ic("文本被分割成的块数：", len(source_text_chunks))
        translated_chunks: List[str] = list(
            await asyncio.gather(*[process_chunk(chunk, index) for index, chunk in enumerate(source_text_chunks)]))

        # 如果有失败的块，再次尝试处理
        if failed_chunks:
            ic("重试失败的块")
            retry_results = await asyncio.gather(*[process_chunk(chunk, index) for index, chunk in failed_chunks])

            for (index, _), retry_result in zip(failed_chunks, retry_results):
                translated_chunks[index] = retry_result  # 放回原来的位置

        return "\n\n".join(translated_chunks)
