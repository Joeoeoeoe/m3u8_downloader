
import re
from itertools import product

class SimpleUrlParser:
    def __init__(self):
        pass

    def parse_input_string(self, input_str):
        """
        解析输入字符串，提取 URL 模板和所有的替换规则。
        期望格式示例： "https://example.com/file_{{idx}}.zip {{idx:1-10}} {{month:1,2,3}}"
        或者 "https://example.com/path/to/resource.txt {{name:file1,file2}}"
        """
        input_str = input_str.strip()

        # 使用正则表达式查找第一个 {{key:value}} 模式的起始位置
        # 这个模式之前的都是 URL Template
        match = re.search(r'\s+\{\{(\w+?):(.+?)\}\}', input_str)

        if match:
            # URL 模板是第一个匹配项之前的部分，去除尾部空格
            url_template = input_str[:match.start()].strip()
            # 规则字符串是第一个匹配项之后的部分，包含所有规则
            rules_str = input_str[match.start():].strip()
        else:
            # 如果没有找到替换规则，则整个字符串都是 URL 模板
            url_template = input_str
            rules_str = ""

        # 解析所有规则
        replacements_data = {}
        # 查找所有形如 {{key:value_spec}} 的规则
        rule_matches = re.findall(r'\{\{(\w+?):(.+?)\}\}', rules_str)

        for key, value_spec in rule_matches:
            key = key.strip()
            value_spec = value_spec.strip()

            # 判断是范围还是列表
            if '-' in value_spec and re.match(r'^\d+-\d+$', value_spec):
                # 范围解析: "1-10"
                start, end = map(int, value_spec.split('-'))
                replacements_data[key] = list(range(start, end + 1))
            else:
                # 列表解析: "item1,item2,item3"
                replacements_data[key] = [item.strip() for item in value_spec.split(',')]

        # 提取 URL 模板中定义的占位符（{{key}}）
        # 确保这些占位符在 replacements_data 中有对应的规则
        # 这样我们可以按正确的顺序生成组合
        placeholders_in_template = re.findall(r'\{\{(\w+?)\}\}', url_template)

        # 确保所有模板中的占位符都有对应的规则
        for ph in placeholders_in_template:
            if ph not in replacements_data:
                print(f"Warning: Placeholder '{{{{{ph}}}}}' in URL template has no matching rule. It will be treated as raw text.")
                # 可以选择在这里报错或忽略，取决于你的严格程度
                # 忽略意味着 {{nomatch}} 就会 그대로 남아있게 됨
                # 这里我们假设如果没匹配上，那就不会在 product 中出现


        return url_template, replacements_data, placeholders_in_template

    def generate_urls(self, url_template, replacements_data, placeholders_in_template):
        """
        根据 URL 模板、替换数据和占位符顺序生成最终 URL 列表。
        """
        generated_urls = []

        if not placeholders_in_template or not replacements_data:
            # 如果没有需要替换的占位符，或没有提供替换规则，则直接返回原始 URL 模板
            return [url_template]

        # 确保按照占位符在模板中出现的顺序来取值列表
        ordered_value_lists = []
        for ph in placeholders_in_template:
            if ph in replacements_data:
                ordered_value_lists.append(replacements_data[ph])
            else:
                # 如果模板中有占位符但没有对应的规则，则忽略这个占位符的生成
                # 这应该在 parse_input_string 中被警告
                print(f"Error: Placeholder '{{{{{ph}}}}}' in template missing replacement data.")
                return [] # 无法生成，返回空列表

        # 生成所有组合
        for combo in product(*ordered_value_lists):
            current_url = url_template
            # 遍历占位符，按顺序替换
            for i, ph_value in enumerate(combo):
                placeholder_key = placeholders_in_template[i]

                # 动态填充逻辑 (可选):
                # 如果是数字，且是范围生成，可以根据最大值进行填充
                ph_str_val = str(ph_value)
                if isinstance(ph_value, int):
                    # 查找这个 key 对应的所有值中最大的数字的位数，进行填充
                    if placeholder_key in replacements_data and isinstance(replacements_data[placeholder_key][0], int):
                        max_val_in_range = max(replacements_data[placeholder_key])
                        padding_len = len(str(max_val_in_range))
                        ph_str_val = str(ph_value).zfill(padding_len)

                current_url = current_url.replace(f"{{{{{placeholder_key}}}}}", ph_str_val, 1) # 只替换一次，防止多重替换错误
            generated_urls.append(current_url)

        return generated_urls

# --- 使用示例 ---
if __name__ == "__main__":
    parser = SimpleUrlParser()

    # 示例 1: 数字范围
    input_string_1 = "https://example.com/images/pic_{{idx}}.jpg {{idx:1-5}}"
    url_template_1, replacements_data_1, placeholders_1 = parser.parse_input_string(input_string_1)
    print(f"URL Template 1: {url_template_1}")
    print(f"Replacements Data 1: {replacements_data_1}")
    print(f"Placeholders 1: {placeholders_1}")
    urls_1 = parser.generate_urls(url_template_1, replacements_data_1, placeholders_1)
    print("Generated URLs 1:")
    for url in urls_1:
        print(url)
    print("-" * 30)

    # 示例 2: 列表
    input_string_2 = "https://example.com/data_{{type}}.json {{type:users,products,orders}}"
    url_template_2, replacements_data_2, placeholders_2 = parser.parse_input_string(input_string_2)
    print(f"URL Template 2: {url_template_2}")
    print(f"Replacements Data 2: {replacements_data_2}")
    print(f"Placeholders 2: {placeholders_2}")
    urls_2 = parser.generate_urls(url_template_2, replacements_data_2, placeholders_2)
    print("Generated URLs 2:")
    for url in urls_2:
        print(url)
    print("-" * 30)

    # 示例 3: 多个占位符
    input_string_3 = "https://example.com/archive/{{year}}/month_{{month}}-page_{{page}}.zip {{year:2022-2023}} {{month:1,2}} {{page:1-3}}"
    url_template_3, replacements_data_3, placeholders_3 = parser.parse_input_string(input_string_3)
    print(f"URL Template 3: {url_template_3}")
    print(f"Replacements Data 3: {replacements_data_3}")
    print(f"Placeholders 3: {placeholders_3}")
    urls_3 = parser.generate_urls(url_template_3, replacements_data_3, placeholders_3)
    print("Generated URLs 3:")
    for url in urls_3:
        print(url)
    print("-" * 30)

    # 示例 4: 没有替换规则的普通 URL
    input_string_4 = "https://example.com/single_file.txt"
    url_template_4, replacements_data_4, placeholders_4 = parser.parse_input_string(input_string_4)
    print(f"URL Template 4: {url_template_4}")
    print(f"Replacements Data 4: {replacements_data_4}")
    print(f"Placeholders 4: {placeholders_4}")
    urls_4 = parser.generate_urls(url_template_4, replacements_data_4, placeholders_4)
    print("Generated URLs 4:")
    for url in urls_4:
        print(url)
    print("-" * 30)

    # 示例 5: URL 中有占位符，但规则文件中没有定义
    input_string_5 = "https://example.com/file_{{undefined}}.pdf {{idx:1-2}}"
    url_template_5, replacements_data_5, placeholders_5 = parser.parse_input_string(input_string_5)
    print(f"URL Template 5: {url_template_5}")
    print(f"Replacements Data 5: {replacements_data_5}")
    print(f"Placeholders 5: {placeholders_5}")
    urls_5 = parser.generate_urls(url_template_5, replacements_data_5, placeholders_5)
    print("Generated URLs 5:")
    for url in urls_5:
        print(url)
    print("-" * 30)
