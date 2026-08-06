"""Session management commands for FrameLearn.

Provides CLI commands to inspect and clean up the session database:
  - list: Show all sessions
  - delete <session_id>: Delete a specific session
  - clear: Delete all sessions
  - info: Show database statistics
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional


def _get_db_path() -> str:
    """Get session database path from env or default location."""
    return os.getenv(
        "FRAMELEARN_SESSION_DB",
        str(Path.home() / ".framelearn" / "sessions.db")
    )


def _ensure_db_exists() -> bool:
    """Check if database exists."""
    return Path(_get_db_path()).exists()


def list_sessions():
    """List all sessions with metadata."""
    if not _ensure_db_exists():
        print("📭 会话数据库不存在（尚未创建任何会话）")
        return

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        cursor = conn.execute("""
            SELECT 
                s.id,
                s.title,
                s.thread_id,
                s.created_at,
                s.updated_at,
                COUNT(m.id) as message_count
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
        """)
        
        sessions = cursor.fetchall()
        
        if not sessions:
            print("📭 暂无会话记录")
            return
        
        print(f"📚 共 {len(sessions)} 个会话：\n")
        
        for s in sessions:
            from datetime import datetime
            updated = datetime.fromtimestamp(s["updated_at"]).strftime("%Y-%m-%d %H:%M:%S")
            title = s["title"] or "(无标题)"
            print(f"  • {s['id']}")
            print(f"    标题: {title}")
            print(f"    消息数: {s['message_count']}")
            print(f"    更新: {updated}")
            if s["thread_id"]:
                print(f"    Thread: {s['thread_id']}")
            print()
    
    finally:
        conn.close()


def delete_session(session_id: str):
    """Delete a specific session and its messages."""
    if not _ensure_db_exists():
        print("❌ 会话数据库不存在")
        return
    
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    
    try:
        # Check if session exists
        cursor = conn.execute("SELECT id, title FROM sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        
        if not session:
            print(f"❌ 会话 {session_id} 不存在")
            return
        
        # Delete messages first (foreign key constraint)
        cursor = conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        msg_count = cursor.rowcount
        
        # Delete session
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        
        print(f"✅ 已删除会话 {session_id}")
        print(f"   删除了 {msg_count} 条消息")
    
    finally:
        conn.close()


def clear_all_sessions(confirm: bool = False):
    """Delete all sessions and messages."""
    if not _ensure_db_exists():
        print("📭 会话数据库不存在（无需清理）")
        return
    
    if not confirm:
        print("⚠️  此操作将删除所有会话历史，不可恢复！")
        response = input("确认清空所有会话？(yes/no): ").strip().lower()
        if response not in ("yes", "y", "是"):
            print("❌ 已取消")
            return
    
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    
    try:
        # Get counts before deletion
        msg_cursor = conn.execute("SELECT COUNT(*) FROM messages")
        msg_count = msg_cursor.fetchone()[0]
        
        sess_cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        sess_count = sess_cursor.fetchone()[0]
        
        # Delete all
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        
        print(f"✅ 已清空所有会话")
        print(f"   删除了 {sess_count} 个会话、{msg_count} 条消息")
        
        # Optimize database (reclaim disk space)
        conn.execute("VACUUM")
        print("   数据库已压缩")
    
    finally:
        conn.close()


def show_info():
    """Show database statistics and disk usage."""
    db_path = _get_db_path()
    
    if not Path(db_path).exists():
        print("📭 会话数据库不存在")
        print(f"   位置: {db_path}")
        return
    
    # File size
    size_bytes = Path(db_path).stat().st_size
    size_kb = size_bytes / 1024
    size_mb = size_kb / 1024
    
    if size_mb >= 1:
        size_str = f"{size_mb:.2f} MB"
    else:
        size_str = f"{size_kb:.2f} KB"
    
    print(f"💾 会话数据库信息")
    print(f"   位置: {db_path}")
    print(f"   大小: {size_str} ({size_bytes:,} bytes)")
    print()
    
    # Database statistics
    conn = sqlite3.connect(db_path)
    
    try:
        # Session count
        cursor = conn.execute("SELECT COUNT(*) FROM sessions")
        sess_count = cursor.fetchone()[0]
        
        # Message count
        cursor = conn.execute("SELECT COUNT(*) FROM messages")
        msg_count = cursor.fetchone()[0]
        
        # Message breakdown by role
        cursor = conn.execute("""
            SELECT role, COUNT(*) as count 
            FROM messages 
            GROUP BY role 
            ORDER BY count DESC
        """)
        role_stats = cursor.fetchall()
        
        # Oldest and newest
        cursor = conn.execute("""
            SELECT MIN(created_at), MAX(updated_at) FROM sessions
        """)
        oldest, newest = cursor.fetchone()
        
        print(f"📊 统计信息")
        print(f"   会话数: {sess_count}")
        print(f"   消息总数: {msg_count}")
        print()
        
        if role_stats:
            print(f"   消息分布:")
            for role, count in role_stats:
                print(f"     • {role}: {count}")
            print()
        
        if oldest and newest:
            from datetime import datetime
            oldest_dt = datetime.fromtimestamp(oldest).strftime("%Y-%m-%d %H:%M:%S")
            newest_dt = datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S")
            print(f"   最早会话: {oldest_dt}")
            print(f"   最近更新: {newest_dt}")
    
    finally:
        conn.close()


def export_session(session_id: str, output_path: Optional[str] = None):
    """Export a session to JSON format."""
    if not _ensure_db_exists():
        print("❌ 会话数据库不存在")
        return
    
    import json
    from datetime import datetime
    
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Get session metadata
        cursor = conn.execute("""
            SELECT id, title, thread_id, created_at, updated_at
            FROM sessions WHERE id = ?
        """, (session_id,))
        session = cursor.fetchone()
        
        if not session:
            print(f"❌ 会话 {session_id} 不存在")
            return
        
        # Get messages
        cursor = conn.execute("""
            SELECT role, content, tool_calls, tool_call_id, reasoning, 
                   codex_thread_id, codex_turn_id, provider_item_id, created_at
            FROM messages 
            WHERE session_id = ?
            ORDER BY created_at
        """, (session_id,))
        messages = cursor.fetchall()
        
        # Build export object
        export_data = {
            "session": {
                "id": session["id"],
                "title": session["title"],
                "thread_id": session["thread_id"],
                "created_at": datetime.fromtimestamp(session["created_at"]).isoformat(),
                "updated_at": datetime.fromtimestamp(session["updated_at"]).isoformat(),
            },
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "tool_calls": json.loads(m["tool_calls"]) if m["tool_calls"] else None,
                    "tool_call_id": m["tool_call_id"],
                    "reasoning": json.loads(m["reasoning"]) if m["reasoning"] else None,
                    "codex_thread_id": m["codex_thread_id"],
                    "codex_turn_id": m["codex_turn_id"],
                    "provider_item_id": m["provider_item_id"],
                    "created_at": datetime.fromtimestamp(m["created_at"]).isoformat(),
                }
                for m in messages
            ]
        }
        
        # Write to file or stdout
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            print(f"✅ 已导出到 {output_path}")
        else:
            print(json.dumps(export_data, ensure_ascii=False, indent=2))
    
    finally:
        conn.close()
