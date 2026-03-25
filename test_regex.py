import re

def auto_format_ingredients(ingr_text: str, allergens_text: str) -> str:
    if not ingr_text:
        return ""
        
    text = ingr_text
    
    # 1. 自动前缀加粗 
    pattern_prefix1 = r'(\[[A-Z]{2}\]\s*[A-Za-z\u00C0-\u017F]+:)'
    text = re.sub(pattern_prefix1, r'**\1**', text)
    
    # 支持无括号 / 开头的
    pattern_prefix2 = r'(^|/\s*)([A-Z][a-z\u00C0-\u017F]+:)'
    text = re.sub(pattern_prefix2, r'\1**\2**', text)
    
    # 2. 过敏原
    if allergens_text:
        tokens = [t.strip() for t in re.split(r'[,;]', allergens_text) if t.strip()]
        if tokens:
            tokens.sort(key=len, reverse=True)
            escaped = [re.escape(t) for t in tokens]
            
            # 使用更宽泛的边界：(?<![a-zA-Z\u00C0-\u017F]) 确保左右不是字母
            pattern_allergens = r'(?<![a-zA-Z\u00C0-\u017F])(' + '|'.join(escaped) + r')(?![a-zA-Z\u00C0-\u017F])'
            text = re.sub(pattern_allergens, lambda m: f"**__{m.group(1)}__**", text, flags=re.IGNORECASE)
            
    return text

sample_ingr = "[EN] Ingredients: Water, Soybeans(23%), Sugar, Salt, Wheat(Gluten)(11%), Mushroom Extract(0.002%). / [NL] Ingrediënten: Water, Sojabonen(23%), Suiker, Zout, Tarwe(Gluten)(11%), Paddenstoelenextract(0.002%). / [ES] Ingredientes: Agua, Soja(23%), Azúcar... / Zutaten: Wasser, Sojabohnen..."
sample_allergens = "Soybeans, Wheat(Gluten), Sojabonen, Tarwe(Gluten), Soja, Sojabohnen"

print("BEFORE:")
print(sample_ingr)
print("\nAFTER:")
print(auto_format_ingredients(sample_ingr, sample_allergens))
