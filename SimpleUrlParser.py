
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

        # 按模板中的占位符顺序取值；若某占位符缺失规则，则保留原文占位符。
        ordered_value_lists, _ = self._build_ordered_value_lists(placeholders_in_template, replacements_data)

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

    @staticmethod
    def _placeholder_literal(placeholder_key):
        return f"{{{{{placeholder_key}}}}}"

    def _build_ordered_value_lists(self, placeholders_in_template, replacements_data):
        ordered_value_lists = []
        placeholders_with_rules = {}
        for ph in placeholders_in_template:
            values = replacements_data.get(ph)
            if isinstance(values, list) and len(values) > 0:
                ordered_value_lists.append(values)
                placeholders_with_rules[ph] = True
                continue

            print(
                f"Warning: Placeholder '{{{{{ph}}}}}' in template missing replacement data. "
                "Keep raw placeholder text."
            )
            ordered_value_lists.append([self._placeholder_literal(ph)])
            placeholders_with_rules[ph] = False
        return ordered_value_lists, placeholders_with_rules

    def generate_urls_with_match_strings(self, url_template, replacements_data, placeholders_in_template):
        """
        根据 URL 模板、替换数据和占位符顺序生成最终 URL 列表，
        同时为每个 URL 生成一个匹配字符串 (例如: idx_1_epi_2)。
        返回一个 (url, match_string) 元组的列表。
        """
        generated_results = []
        if not placeholders_in_template or not replacements_data:
            # 如果没有需要替换的占位符，或没有提供替换规则
            # match_string 为空
            return [(url_template, "")]
        ordered_value_lists, placeholders_with_rules = self._build_ordered_value_lists(
            placeholders_in_template,
            replacements_data,
        )
        # 生成所有组合
        for combo in product(*ordered_value_lists):
            current_url = url_template
            match_parts = [] # 用于构建匹配字符串的部件列表
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

                # 为匹配字符串添加部件
                if placeholders_with_rules.get(placeholder_key, False):
                    match_parts.append(f"{placeholder_key}_{ph_str_val}")

            # 构建最终的匹配字符串
            match_string = "_".join(match_parts)
            generated_results.append((current_url, match_string))
        return generated_results

# --- 使用示例 ---
if __name__ == "__main__":
    parser = SimpleUrlParser()
    print("--- 示例 1: 数字范围 (带匹配字符串) ---")
    input_string_1 = "https://example.com/images/pic_{{idx}}.jpg {{idx:1-5}}"
    url_template_1, replacements_data_1, placeholders_1 = parser.parse_input_string(input_string_1)
    print(f"URL Template 1: {url_template_1}")
    print(f"Replacements Data 1: {replacements_data_1}")
    print(f"Placeholders 1: {placeholders_1}")
    results_1 = parser.generate_urls_with_match_strings(url_template_1, replacements_data_1, placeholders_1)
    print("Generated URLs & Match Strings 1:")
    for url, match_str in results_1:
        print(f"URL: {url}, Match: {match_str}")
    print("-" * 30)
    print("\n--- 示例 2: 列表 (带匹配字符串) ---")
    input_string_2 = "https://example.com/data_{{type}}.json {{type:users,products,orders}}"
    url_template_2, replacements_data_2, placeholders_2 = parser.parse_input_string(input_string_2)
    print(f"URL Template 2: {url_template_2}")
    print(f"Replacements Data 2: {replacements_data_2}")
    print(f"Placeholders 2: {placeholders_2}")
    results_2 = parser.generate_urls_with_match_strings(url_template_2, replacements_data_2, placeholders_2)
    print("Generated URLs & Match Strings 2:")
    for url, match_str in results_2:
        print(f"URL: {url}, Match: {match_str}")
    print("-" * 30)
    print("\n--- 示例 3: 多个占位符 (带匹配字符串) ---")
    input_string_3 = "https://example.com/archive/{{year}}/month_{{month}}-page_{{page}}.zip {{year:2022-2023}} {{month:1,2}} {{page:1-3}}"
    url_template_3, replacements_data_3, placeholders_3 = parser.parse_input_string(input_string_3)
    results_3 = parser.generate_urls_with_match_strings(url_template_3, replacements_data_3, placeholders_3)
    print("Generated URLs & Match Strings 3:")
    for url, match_str in results_3:
        print(f"URL: {url}, Match: {match_str}")
    print("-" * 30)
    print("\n--- 示例 4: 没有替换规则的普通 URL (带匹配字符串) ---")
    input_string_4 = "https://example.com/single_file.txt"
    url_template_4, replacements_data_4, placeholders_4 = parser.parse_input_string(input_string_4)
    results_4 = parser.generate_urls_with_match_strings(url_template_4, replacements_data_4, placeholders_4)
    print("Generated URLs & Match Strings 4:")
    for url, match_str in results_4:
        print(f"URL: {url}, Match: {match_str}")
    print("-" * 30)
    print("\n--- 示例 5: URL 中有占位符，但规则文件中没有定义 (带匹配字符串) ---")
    input_string_5 = "https://example.com/file_{{undefined}}.pdf {{idx:1-2}}"
    url_template_5, replacements_data_5, placeholders_5 = parser.parse_input_string(input_string_5)
    results_5 = parser.generate_urls_with_match_strings(url_template_5, replacements_data_5, placeholders_5)
    print("Generated URLs & Match Strings 5:")
    for url, match_str in results_5:
        print(f"URL: {url}, Match: {match_str}")
    print("-" * 30)
