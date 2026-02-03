# 读取 server.py
with open('server.py', 'r') as f:
    lines = f.readlines()

# 找到插入位置（在 on_interaction 函数的最后）
insert_index = -1
for i, line in enumerate(lines):
    if 'await SIO.emit("log", response)  # send this to all' in line:
        # 找到下一行 await SIO.emit("interaction_response"...)
        if i + 1 < len(lines) and 'await SIO.emit("interaction_response", response, room=sid)' in lines[i + 1]:
            insert_index = i + 2
            break

if insert_index == -1:
    print("❌ Could not find insertion point!")
    print("Please add code manually after line containing:")
    print('  await SIO.emit("interaction_response", response, room=sid)')
    exit(1)

# 准备要插入的代码
socratic_code = '''
    # ========== Socratic Question Auto-Trigger ==========
    if SOCRATIC_ENGINE:  # Only execute if engine initialized successfully
        try:
            user_id = data.get("participantId") or sid
            
            # Initialize user interaction history
            if user_id not in USER_INTERACTION_HISTORY:
                USER_INTERACTION_HISTORY[user_id] = []
                print(f"\\n[SOCRATIC] New user session: {user_id}")
            
            # Add current interaction to history
            USER_INTERACTION_HISTORY[user_id].append(data)
            
            # === Detailed debugging ===
            history_len = len(USER_INTERACTION_HISTORY[user_id])
            interaction_type = data.get('interactionType')
            print(f"[SOCRATIC] User: {user_id[:8]}... | Step: {history_len} | Type: {interaction_type}")
            
            # Build current context from interaction data
            current_context = {
                'x_attribute': data.get('x_attribute'),
                'y_attribute': data.get('y_attribute'),
                'chart_type': data.get('chart_changed'),
                'filters_active': []
            }
            
            # Process interaction and check if question should be triggered
            result = await SOCRATIC_ENGINE.process_interaction(
                user_id,
                data,
                current_context
            )
            
            # If a question was triggered, send it to the frontend
            if result.get('should_ask'):
                question_payload = {
                    'questionId': f"auto_{datetime.now().timestamp()}",
                    'category': result['category'],
                    'question': result['question'],
                    'method': result['method'],
                    'triggerDetails': result.get('trigger_details', {}),
                    'sessionInfo': result.get('session_info', {})
                }
                
                # Emit question to frontend via Socket.IO
                await SIO.emit('socratic_question_triggered', question_payload, room=sid)
                
                print(f"\\n{'='*60}")
                print(f"✓ SOCRATIC QUESTION TRIGGERED!")
                print(f"{'='*60}")
                print(f"  User: {user_id}")
                print(f"  Step: {history_len}")
                print(f"  Category: {result['category']}")
                print(f"  Question: {result['question'][:80]}...")
                print(f"  Method: {result['method']}")
                print(f"  Confidence: {result['trigger_details'].get('confidence', 0):.2f}")
                print(f"{'='*60}\\n")
            else:
                # Show status every 10 steps
                if history_len % 10 == 0:
                    print(f"[SOCRATIC] Step {history_len}: {result.get('reason', 'checking...')}")
        
        except Exception as e:
            # Silent failure - don't break existing functionality
            print(f"✗ Socratic Engine error: {e}")
            import traceback
            traceback.print_exc()
'''

# 插入代码
lines.insert(insert_index, socratic_code)

# 写回文件
with open('server.py', 'w') as f:
    f.writelines(lines)

print(f"✓ Successfully added Socratic Engine code to server.py")
print(f"  Inserted at line {insert_index}")
print(f"  Please restart the server: python server.py")
