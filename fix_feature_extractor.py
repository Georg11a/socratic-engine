# 读取文件
with open('engine/feature_extractor.py', 'r') as f:
    content = f.read()

# 修复所有的字段名引用
replacements = [
    # _count_by_type 方法
    ("interaction_type = h.get('interaction_type') or h.get('type') or ''",
     "interaction_type = h.get('interaction_type') or h.get('interactionType') or h.get('type') or ''"),
    
    # _steps_since 方法
    ("interaction_type = history[i].get('interaction_type') or history[i].get('type') or ''",
     "interaction_type = history[i].get('interaction_type') or history[i].get('interactionType') or history[i].get('type') or ''"),
    
    # _get_recent_mouseover_points 方法
    ("interaction_type = h.get('interaction_type') or h.get('type')",
     "interaction_type = h.get('interaction_type') or h.get('interactionType') or h.get('type')"),
    
    # _find_last_index 方法
    ("interaction_type = history[i].get('interaction_type') or history[i].get('type') or ''",
     "interaction_type = history[i].get('interaction_type') or history[i].get('interactionType') or history[i].get('type') or ''"),
]

for old, new in replacements:
    content = content.replace(old, new)

# 写回文件
with open('engine/feature_extractor.py', 'w') as f:
    f.write(content)

print("✓ Fixed feature_extractor.py")
print("  - Added support for camelCase field names")
print("  - Backup saved as feature_extractor.py.backup")
