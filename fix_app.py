with open('app.py', 'r') as f:
    lines = f.readlines()

# The original file ended at line 494:         st.info("请在左侧选择或粘贴 PLM JSON 数据，点击「生成标签」查看排版预览。")\n
new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if line.strip() == 'st.info("请在左侧选择或粘贴 PLM JSON 数据，点击「生成标签」查看排版预览。")':
        break

with open('app.py', 'w') as f:
    f.writelines(new_lines)

print("Fixed app.py. Total lines:", len(new_lines))
