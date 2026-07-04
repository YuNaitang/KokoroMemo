"""统一存储仓库层。file 模式委托现有 SQLite 函数，server 模式使用 SQLAlchemy ORM。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.storage.database import is_server_mode, get_engine
from app.storage.vector_base import VectorStore

logger = logging.getLogger("kokoromemo.repository")

_repository: StorageRepository | None = None


class StorageRepository:
    """存储仓库，封装所有数据访问。"""

    def __init__(self):
        self._db_path: str = str(Path("/app/data") / "app.sqlite")
        self._server_mode = is_server_mode()
        self._vector_store: VectorStore | None = None
        self._engine = get_engine() if self._server_mode else None

    # ========================================================================
    # Card 数据访问
    # ========================================================================

    async def get_card(self, card_id: str) -> dict | None:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(MemoryCard).where(MemoryCard.card_id == card_id)
                )
                row = result.fetchone()
                return dict(row._mapping) if row else None
        from app.storage.sqlite_cards import get_cards_by_ids
        cards = await get_cards_by_ids(self._db_path, [card_id])
        return cards.get(card_id)

    async def list_cards(
        self,
        scope: str | None = None,
        character_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            query = select(MemoryCard)
            if scope:
                query = query.where(MemoryCard.scope == scope)
            if character_id:
                query = query.where(MemoryCard.character_id == character_id)
            if status:
                query = query.where(MemoryCard.status == status)
            async with self._engine.connect() as conn:
                result = await conn.execute(query)
                return [dict(r._mapping) for r in result.fetchall()]
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            where_clauses = []
            params: list[Any] = []
            if scope:
                where_clauses.append("scope = ?")
                params.append(scope)
            if character_id:
                where_clauses.append("character_id = ?")
                params.append(character_id)
            if status:
                where_clauses.append("status = ?")
                params.append(status)
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            cursor = await db.execute(
                f"SELECT * FROM memory_cards WHERE {where_sql} ORDER BY updated_at DESC",
                params,
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_cards_by_ids(self, card_ids: list[str]) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(MemoryCard).where(MemoryCard.card_id.in_(card_ids))
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_cards_by_ids
        cards = await get_cards_by_ids(self._db_path, card_ids)
        return list(cards.values())

    async def get_approved_cards(self) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(MemoryCard).where(MemoryCard.status == "approved")
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_approved_cards
        return await get_approved_cards(self._db_path)

    async def get_pinned_cards(
        self,
        user_id: str = "default",
        character_id: str | None = None,
    ) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import or_, select
            query = select(MemoryCard).where(
                MemoryCard.status == "approved",
                or_(MemoryCard.is_pinned == 1, MemoryCard.card_type == "boundary"),
            )
            if character_id:
                query = query.where(
                    or_(
                        MemoryCard.character_id == character_id,
                        MemoryCard.character_id.is_(None),
                    )
                )
            async with self._engine.connect() as conn:
                result = await conn.execute(query)
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_pinned_cards
        return await get_pinned_cards(self._db_path, user_id, character_id)

    async def get_recent_important_cards(
        self,
        user_id: str = "default",
        limit: int = 10,
    ) -> list[dict]:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(MemoryCard)
                    .where(
                        MemoryCard.status == "approved",
                        MemoryCard.importance >= 0.75,
                    )
                    .order_by(MemoryCard.importance.desc(), MemoryCard.created_at.desc())
                    .limit(limit)
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_recent_important_cards
        return await get_recent_important_cards(self._db_path, user_id, None, limit=limit)

    async def insert_card(self, card: dict) -> str:
        card_id = card.get("card_id", "")
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import insert as sql_insert
            _CARD_COLS = {
                "card_id", "library_id", "user_id", "character_id", "conversation_id",
                "scope", "card_type", "title", "content", "summary", "importance",
                "confidence", "stability", "status", "is_pinned",
                "embedding_model", "embedding_dimension", "vector_synced",
            }
            now = datetime.now().isoformat()
            values = {k: v for k, v in card.items() if k in _CARD_COLS}
            values.setdefault("card_id", card_id)
            values["created_at"] = now
            values["updated_at"] = now
            stmt = sql_insert(MemoryCard).values(**values)
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            return card_id or values.get("card_id", "")
        from app.storage.sqlite_cards import insert_card as _insert_card
        await _insert_card(
            self._db_path,
            card_id=card.get("card_id", ""),
            user_id=card.get("user_id", ""),
            character_id=card.get("character_id"),
            conversation_id=card.get("conversation_id"),
            scope=card.get("scope", ""),
            card_type=card.get("card_type", ""),
            content=card.get("content", ""),
            title=card.get("title"),
            summary=card.get("summary"),
            importance=card.get("importance", 0.5),
            confidence=card.get("confidence", 0.7),
            status=card.get("status", "pending_review"),
            is_pinned=card.get("is_pinned", 0),
            evidence_text=card.get("evidence_text"),
            supersedes_card_id=card.get("supersedes_card_id"),
            library_id=card.get("library_id"),
        )
        return card_id

    async def insert_card_version(self, card_id: str, card: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO memory_card_versions
                    (version_id, card_id, version_number, content, summary, card_type,
                     importance, confidence, created_at)
                VALUES (:version_id, :card_id, :version_number, :content, :summary,
                        :card_type, :importance, :confidence, :created_at)
            """)
            async with self._engine.begin() as conn:
                # 计算 version_number
                result = await conn.execute(
                    text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM memory_card_versions WHERE card_id = :cid"),
                    {"cid": card_id},
                )
                version_number = result.scalar()
                from app.core.ids import generate_id
                params = {
                    "version_id": generate_id("ver_"),
                    "card_id": card_id,
                    "version_number": version_number,
                    "content": card.get("content", ""),
                    "summary": card.get("summary"),
                    "card_type": card.get("card_type", ""),
                    "importance": card.get("importance", 0.5),
                    "confidence": card.get("confidence", 0.7),
                    "created_at": datetime.now().isoformat(),
                }
                await conn.execute(stmt, params)
            return
        from app.storage.sqlite_cards import insert_card_version as _insert_version
        await _insert_version(
            self._db_path,
            card_id=card_id,
            content=card.get("content", ""),
            card_type=card.get("card_type", ""),
            summary=card.get("summary"),
            importance=card.get("importance", 0.5),
            confidence=card.get("confidence", 0.7),
        )

    async def update_card(self, card_id: str, updates: dict) -> bool:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import update as sql_update
            _CARD_COLS = {
                "library_id", "user_id", "character_id", "conversation_id",
                "scope", "card_type", "title", "content", "summary", "importance",
                "confidence", "stability", "status", "is_pinned",
                "embedding_model", "embedding_dimension", "vector_synced",
            }
            filtered = {k: v for k, v in updates.items() if k in _CARD_COLS}
            if not filtered:
                return False
            filtered["updated_at"] = datetime.now().isoformat()
            stmt = (
                sql_update(MemoryCard)
                .where(MemoryCard.card_id == card_id)
                .values(**filtered)
            )
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
                return result.rowcount > 0
        import aiosqlite
        allowed_cols = {
            "library_id", "user_id", "character_id", "conversation_id", "scope",
            "card_type", "title", "content", "summary", "importance", "confidence",
            "stability", "status", "is_pinned", "evidence_text", "supersedes_card_id",
            "embedding_model", "embedding_dimension", "vector_synced",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_cols}
        if not filtered:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        filtered["updated_at"] = datetime.now().isoformat()
        set_clause += ", updated_at = ?"
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                f"UPDATE memory_cards SET {set_clause} WHERE card_id = ?",
                list(filtered.values()) + [card_id],
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_card(self, card_id: str) -> bool:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import update as sql_update
            stmt = (
                sql_update(MemoryCard)
                .where(MemoryCard.card_id == card_id)
                .values(status="deleted", updated_at=datetime.now().isoformat())
            )
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
                return result.rowcount > 0
        from app.storage.sqlite_cards import update_card_status
        await update_card_status(self._db_path, card_id, "deleted")
        return True

    async def card_exists_with_content(self, user_id: str, content: str) -> bool:
        if self._server_mode:
            from app.storage.models import MemoryCard
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(MemoryCard.card_id)
                    .where(
                        MemoryCard.user_id == user_id,
                        MemoryCard.content == content,
                        MemoryCard.status != "deleted",
                    )
                    .limit(1)
                )
                return result.fetchone() is not None
        from app.storage.sqlite_cards import card_exists_with_content
        return await card_exists_with_content(self._db_path, user_id, content)

    # ── 记忆库与挂载 ──

    async def list_memory_libraries(self, include_deleted: bool = False) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            where = "" if include_deleted else "WHERE l.status = 'active'"
            stmt = text(f"""
                SELECT l.*, COUNT(c.card_id) AS card_count
                FROM memory_libraries l
                LEFT JOIN memory_cards c ON c.library_id = l.library_id AND c.status != 'deleted'
                {where}
                GROUP BY l.library_id
                ORDER BY l.is_builtin DESC, l.updated_at DESC
            """)
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt)
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import list_memory_libraries
        return await list_memory_libraries(self._db_path, include_deleted)

    async def create_memory_library(self, name: str, description: str = "") -> str:
        if self._server_mode:
            from app.core.ids import generate_id
            from sqlalchemy import text
            library_id = generate_id("lib_")
            stmt = text("""
                INSERT INTO memory_libraries (library_id, name, description, status, is_builtin, created_at, updated_at)
                VALUES (:library_id, :name, :description, 'active', 0, :now, :now)
                ON CONFLICT(library_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    status = 'active',
                    updated_at = :now
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {"library_id": library_id, "name": name, "description": description, "now": now})
            return library_id
        from app.storage.sqlite_cards import create_memory_library as _create_library
        return await _create_library(self._db_path, name=name, description=description)

    async def update_memory_library(self, library_id: str, name: str, description: str = "") -> bool:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                UPDATE memory_libraries
                SET name = :name, description = :description, updated_at = :now
                WHERE library_id = :library_id
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt, {"library_id": library_id, "name": name, "description": description, "now": now})
                return result.rowcount > 0
        from app.storage.sqlite_cards import update_memory_library
        return await update_memory_library(self._db_path, library_id, name, description)

    async def delete_memory_library(self, library_id: str) -> bool:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                UPDATE memory_libraries
                SET status = 'deleted', updated_at = :now
                WHERE library_id = :library_id AND is_builtin = 0
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt, {"library_id": library_id, "now": now})
                # 同时软删除挂载
                await conn.execute(
                    text("UPDATE conversation_memory_mounts SET status = 'deleted', updated_at = :now WHERE library_id = :library_id"),
                    {"library_id": library_id, "now": now},
                )
                return result.rowcount > 0
        from app.storage.sqlite_cards import delete_memory_library
        return await delete_memory_library(self._db_path, library_id)

    async def get_conversation_mounts(self, conversation_id: str) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                SELECT m.*, l.name, l.description, l.is_builtin
                FROM conversation_memory_mounts m
                JOIN memory_libraries l ON l.library_id = m.library_id
                WHERE m.conversation_id = :conv_id AND m.status = 'active' AND l.status = 'active'
                ORDER BY m.is_write_target DESC, m.sort_order ASC, m.created_at ASC
            """)
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt, {"conv_id": conversation_id})
                rows = [dict(r._mapping) for r in result.fetchall()]
            if not rows:
                await self.set_conversation_mounts(conversation_id, ["lib_default"])
                return await self.get_conversation_mounts(conversation_id)
            return rows
        from app.storage.sqlite_cards import get_conversation_mounts
        return await get_conversation_mounts(self._db_path, conversation_id)

    async def set_conversation_mounts(
        self,
        conversation_id: str,
        library_ids: list[str],
    ) -> None:
        if self._server_mode:
            from app.core.ids import generate_id
            from sqlalchemy import text
            now = datetime.now().isoformat()
            library_ids = list(dict.fromkeys(library_ids))
            if not library_ids:
                library_ids = ["lib_default"]
            write_library_id = library_ids[0]
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("UPDATE conversation_memory_mounts SET status = 'deleted', updated_at = :now WHERE conversation_id = :conv_id"),
                    {"conv_id": conversation_id, "now": now},
                )
                for index, lib_id in enumerate(library_ids):
                    await conn.execute(
                        text("""
                            INSERT INTO conversation_memory_mounts
                                (mount_id, conversation_id, library_id, is_write_target, sort_order, status, created_at, updated_at)
                            VALUES (:mount_id, :conv_id, :lib_id, :is_write, :sort_order, 'active', :now, :now)
                            ON CONFLICT(conversation_id, library_id) WHERE status = 'active' DO UPDATE SET
                                is_write_target = EXCLUDED.is_write_target,
                                sort_order = EXCLUDED.sort_order,
                                status = 'active',
                                updated_at = EXCLUDED.updated_at
                        """),
                        {
                            "mount_id": generate_id("mount_"),
                            "conv_id": conversation_id,
                            "lib_id": lib_id,
                            "is_write": 1 if lib_id == write_library_id else 0,
                            "sort_order": index,
                            "now": now,
                        },
                    )
            return
        from app.storage.sqlite_cards import set_conversation_mounts as _set_mounts
        await _set_mounts(self._db_path, conversation_id, library_ids)

    async def get_mounted_library_ids(self, conversation_id: str) -> list[str]:
        if self._server_mode:
            mounts = await self.get_conversation_mounts(conversation_id)
            return [m["library_id"] for m in mounts]
        from app.storage.sqlite_cards import get_mounted_library_ids
        return await get_mounted_library_ids(self._db_path, conversation_id)

    async def copy_conversation_mounts(self, from_conv: str, to_conv: str) -> None:
        if self._server_mode:
            from app.core.ids import generate_id
            from sqlalchemy import text
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT * FROM conversation_memory_mounts
                        WHERE conversation_id = :conv_id AND status = 'active'
                        ORDER BY sort_order ASC
                    """),
                    {"conv_id": from_conv},
                )
                rows = [dict(r._mapping) for r in result.fetchall()]
                if not rows:
                    return
                now = datetime.now().isoformat()
                await conn.execute(
                    text("UPDATE conversation_memory_mounts SET status = 'deleted', updated_at = :now WHERE conversation_id = :conv_id"),
                    {"conv_id": to_conv, "now": now},
                )
                for row in rows:
                    await conn.execute(
                        text("""
                            INSERT INTO conversation_memory_mounts
                                (mount_id, conversation_id, library_id, user_id, character_id, is_write_target, sort_order, status, created_at, updated_at)
                            VALUES (:mount_id, :conv_id, :lib_id, :user_id, :char_id, :is_write, :sort_order, 'active', :now, :now)
                            ON CONFLICT(conversation_id, library_id) WHERE status = 'active' DO UPDATE SET
                                is_write_target = EXCLUDED.is_write_target,
                                sort_order = EXCLUDED.sort_order,
                                status = 'active',
                                updated_at = EXCLUDED.updated_at
                        """),
                        {
                            "mount_id": generate_id("mount_"),
                            "conv_id": to_conv,
                            "lib_id": row["library_id"],
                            "user_id": row.get("user_id"),
                            "char_id": row.get("character_id"),
                            "is_write": row.get("is_write_target", 0),
                            "sort_order": row.get("sort_order", 0),
                            "now": now,
                        },
                    )
            return
        from app.storage.sqlite_cards import copy_conversation_mounts
        await copy_conversation_mounts(self._db_path, from_conv, to_conv)

    # ── 待审核条目 ──

    async def get_inbox_items(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                SELECT * FROM memory_inbox
                WHERE status = :status
                ORDER BY created_at DESC
                LIMIT :limit
            """)
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt, {"status": status or "pending", "limit": limit})
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_cards import get_inbox_items as _get_inbox
        items, _total = await _get_inbox(self._db_path, status=status or "pending", limit=limit)
        return items

    async def get_inbox_item(self, item_id: str) -> dict | None:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT * FROM memory_inbox WHERE inbox_id = :item_id"),
                    {"item_id": item_id},
                )
                row = result.fetchone()
                return dict(row._mapping) if row else None
        from app.storage.sqlite_cards import get_inbox_item
        return await get_inbox_item(self._db_path, item_id)

    async def insert_inbox_item(self, item: dict) -> str:
        inbox_id = item.get("inbox_id", "")
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO memory_inbox
                    (inbox_id, library_id, candidate_type, payload_json, user_id,
                     character_id, conversation_id, suggested_action, risk_level,
                     reason, status, created_at)
                VALUES (:inbox_id, :library_id, :candidate_type, :payload_json, :user_id,
                        :character_id, :conversation_id, :suggested_action, :risk_level,
                        :reason, :status, :created_at)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "inbox_id": inbox_id,
                    "library_id": item.get("library_id", "lib_default"),
                    "candidate_type": item.get("candidate_type", "card"),
                    "payload_json": item.get("payload_json", "{}"),
                    "user_id": item.get("user_id", ""),
                    "character_id": item.get("character_id"),
                    "conversation_id": item.get("conversation_id"),
                    "suggested_action": item.get("suggested_action", "approve"),
                    "risk_level": item.get("risk_level", "low"),
                    "reason": item.get("reason"),
                    "status": item.get("status", "pending"),
                    "created_at": now,
                })
            return inbox_id
        from app.storage.sqlite_cards import insert_inbox_item as _insert_inbox
        await _insert_inbox(
            self._db_path,
            inbox_id=inbox_id,
            candidate_type=item.get("candidate_type", "card"),
            payload_json=item.get("payload_json", "{}"),
            user_id=item.get("user_id", ""),
            character_id=item.get("character_id"),
            conversation_id=item.get("conversation_id"),
            suggested_action=item.get("suggested_action", "approve"),
            risk_level=item.get("risk_level", "low"),
            reason=item.get("reason"),
            status=item.get("status", "pending"),
            library_id=item.get("library_id"),
        )
        return inbox_id

    async def insert_review_action(self, action: dict) -> None:
        if self._server_mode:
            from app.core.ids import generate_id
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO review_actions (action_id, inbox_id, card_id, action, reviewer, note, created_at)
                VALUES (:action_id, :inbox_id, :card_id, :action, :reviewer, :note, :created_at)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "action_id": generate_id("review_"),
                    "inbox_id": action.get("inbox_id"),
                    "card_id": action.get("card_id"),
                    "action": action.get("action", ""),
                    "reviewer": action.get("reviewer", "local_user"),
                    "note": action.get("note"),
                    "created_at": now,
                })
            return
        from app.storage.sqlite_cards import insert_review_action as _insert_action
        await _insert_action(
            self._db_path,
            action=action.get("action", ""),
            inbox_id=action.get("inbox_id"),
            card_id=action.get("card_id"),
            reviewer=action.get("reviewer", "local_user"),
            note=action.get("note"),
        )

    async def transition_inbox_status(self, item_id: str, new_status: str) -> None:
        if self._server_mode:
            from sqlalchemy import text
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE memory_inbox
                        SET status = :status, reviewed_at = :now, review_note = NULL
                        WHERE inbox_id = :item_id
                    """),
                    {"item_id": item_id, "status": new_status, "now": now},
                )
            return
        from app.storage.sqlite_cards import update_inbox_status
        await update_inbox_status(self._db_path, item_id, new_status)

    async def mark_card_vector_unsynced(self, card_id: str) -> None:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE memory_cards
                        SET vector_synced = 0, vector_synced_at = NULL, updated_at = :now
                        WHERE card_id = :card_id
                    """),
                    {"card_id": card_id, "now": datetime.now().isoformat()},
                )
            return
        from app.storage.sqlite_cards import mark_card_vector_unsynced
        await mark_card_vector_unsynced(self._db_path, card_id)

    # ========================================================================
    # Session / Conversation 数据访问
    # ========================================================================

    async def get_session(self, conversation_id: str) -> dict | None:
        """获取会话记录（来自 conversations 表）。"""
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT * FROM conversations WHERE conversation_id = :conv_id"),
                    {"conv_id": conversation_id},
                )
                row = result.fetchone()
                return dict(row._mapping) if row else None
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def save_raw_request(self, conversation_id: str, data: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO raw_requests (request_id, conversation_id, body_json, headers_json, created_at)
                VALUES (:request_id, :conv_id, :body_json, :headers_json, :created_at)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "request_id": data.get("request_id", ""),
                    "conv_id": conversation_id,
                    "body_json": data.get("body_json", "{}"),
                    "headers_json": data.get("headers_json"),
                    "created_at": now,
                })
            return
        from app.storage.sqlite_conversation import save_raw_request as _save_request
        await _save_request(
            self._db_path,
            request_id=data.get("request_id", ""),
            conversation_id=conversation_id,
            body_json=data.get("body_json", "{}"),
            headers_json=data.get("headers_json"),
        )

    async def save_raw_response(self, conversation_id: str, data: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO raw_responses
                    (response_id, request_id, conversation_id, body_json, stream_text, finish_reason, created_at)
                VALUES (:response_id, :request_id, :conv_id, :body_json, :stream_text, :finish_reason, :created_at)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "response_id": data.get("response_id", ""),
                    "request_id": data.get("request_id", ""),
                    "conv_id": conversation_id,
                    "body_json": data.get("body_json"),
                    "stream_text": data.get("stream_text"),
                    "finish_reason": data.get("finish_reason"),
                    "created_at": now,
                })
            return
        from app.storage.sqlite_conversation import save_raw_response as _save_response
        await _save_response(
            self._db_path,
            response_id=data.get("response_id", ""),
            request_id=data.get("request_id", ""),
            conversation_id=conversation_id,
            body_json=data.get("body_json"),
            stream_text=data.get("stream_text"),
            finish_reason=data.get("finish_reason"),
        )

    async def save_injected_memory_log(self, conversation_id: str, data: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO injected_memory_logs
                    (injection_id, request_id, conversation_id, injected_text, card_ids_json, created_at)
                VALUES (:injection_id, :request_id, :conv_id, :injected_text, :card_ids_json, :created_at)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "injection_id": data.get("injection_id", ""),
                    "request_id": data.get("request_id", ""),
                    "conv_id": conversation_id,
                    "injected_text": data.get("injected_text", ""),
                    "card_ids_json": data.get("card_ids_json"),
                    "created_at": now,
                })
            return
        from app.storage.sqlite_conversation import save_injected_memory_log as _save_log
        await _save_log(
            self._db_path,
            injection_id=data.get("injection_id", ""),
            request_id=data.get("request_id", ""),
            conversation_id=conversation_id,
            injected_text=data.get("injected_text", ""),
            card_ids_json=data.get("card_ids_json"),
        )

    async def save_turn_and_messages(self, conversation_id: str, turn_data: dict, messages: list) -> None:
        if self._server_mode:
            from app.core.ids import generate_id
            from sqlalchemy import text
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                turn_id = turn_data.get("turn_id", generate_id("turn_"))
                await conn.execute(
                    text("""
                        INSERT INTO turns (turn_id, conversation_id, user_id, character_id, request_id, turn_index, created_at)
                        VALUES (:turn_id, :conv_id, :user_id, :char_id, :request_id, :turn_index, :created_at)
                        ON CONFLICT(turn_id) DO NOTHING
                    """),
                    {
                        "turn_id": turn_id,
                        "conv_id": conversation_id,
                        "user_id": turn_data.get("user_id", ""),
                        "char_id": turn_data.get("character_id"),
                        "request_id": turn_data.get("request_id", ""),
                        "turn_index": turn_data.get("turn_index", 0),
                        "created_at": now,
                    },
                )
                for msg in messages:
                    msg_id = generate_id("msg_")
                    await conn.execute(
                        text("""
                            INSERT INTO messages (message_id, turn_id, conversation_id, role, name, content, created_at)
                            VALUES (:msg_id, :turn_id, :conv_id, :role, :name, :content, :created_at)
                            ON CONFLICT(message_id) DO NOTHING
                        """),
                        {
                            "msg_id": msg_id,
                            "turn_id": turn_id,
                            "conv_id": conversation_id,
                            "role": msg.get("role", ""),
                            "name": msg.get("name"),
                            "content": msg.get("content", ""),
                            "created_at": now,
                        },
                    )
            return
        from app.storage.sqlite_conversation import save_turn_and_messages as _save_turn
        await _save_turn(
            self._db_path,
            turn_id=turn_data.get("turn_id", ""),
            conversation_id=conversation_id,
            user_id=turn_data.get("user_id", ""),
            character_id=turn_data.get("character_id"),
            request_id=turn_data.get("request_id", ""),
            turn_index=turn_data.get("turn_index", 0),
            messages=messages,
        )

    async def get_turn_count(self, conversation_id: str) -> int:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT COUNT(*) FROM turns WHERE conversation_id = :conv_id"),
                    {"conv_id": conversation_id},
                )
                return result.scalar() or 0
        from app.storage.sqlite_conversation import get_turn_count
        return await get_turn_count(self._db_path, conversation_id)

    async def get_all_messages(self, conversation_id: str) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("""
                        SELECT role, content FROM messages
                        WHERE conversation_id = :conv_id
                        ORDER BY created_at ASC, message_id ASC
                    """),
                    {"conv_id": conversation_id},
                )
                return [{"role": r["role"], "content": r["content"]} for r in result.mappings().all()]
        from app.storage.sqlite_conversation import get_all_messages
        return await get_all_messages(self._db_path, conversation_id)

    # ========================================================================
    # App-level 数据访问
    # ========================================================================

    async def init_app_db(self) -> None:
        if self._server_mode:
            # server 模式下由 SQLAlchemy ORM 管理 schema
            from app.storage.models import Base
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        from app.storage.sqlite_app import init_app_db as _init_app
        await _init_app(self._db_path)

    async def upsert_character(self, character_id: str, data: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO characters (character_id, user_id, display_name, system_prompt_hash, source, created_at, updated_at)
                VALUES (:character_id, :user_id, :display_name, :system_prompt_hash, :source, :now, :now)
                ON CONFLICT(character_id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    display_name = COALESCE(EXCLUDED.display_name, characters.display_name),
                    system_prompt_hash = COALESCE(EXCLUDED.system_prompt_hash, characters.system_prompt_hash),
                    source = COALESCE(EXCLUDED.source, characters.source)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "character_id": character_id,
                    "user_id": data.get("user_id", "default"),
                    "display_name": data.get("display_name"),
                    "system_prompt_hash": data.get("system_prompt_hash"),
                    "source": data.get("source"),
                    "now": now,
                })
            return
        from app.storage.sqlite_app import upsert_character as _upsert_char
        await _upsert_char(
            self._db_path,
            character_id=character_id,
            user_id=data.get("user_id", "default"),
            display_name=data.get("display_name"),
            system_prompt_hash=data.get("system_prompt_hash"),
            source=data.get("source"),
        )

    async def upsert_conversation(self, conversation_id: str, character_id: str) -> None:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                INSERT INTO conversations (conversation_id, user_id, character_id, path, first_seen_at, last_seen_at)
                VALUES (:conv_id, :user_id, :char_id, :path, :now, :now)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at,
                    character_id = COALESCE(EXCLUDED.character_id, conversations.character_id)
            """)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(stmt, {
                    "conv_id": conversation_id,
                    "user_id": "default",
                    "char_id": character_id,
                    "path": f"conversations/{conversation_id}",
                    "now": now,
                })
            return
        from app.storage.sqlite_app import upsert_conversation as _upsert_conv
        await _upsert_conv(
            self._db_path,
            conversation_id=conversation_id,
            user_id="default",
            character_id=character_id,
            client_name=None,
            conv_path=f"conversations/{conversation_id}",
        )

    async def list_characters(self) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                SELECT c.*, cd.profile_id, cd.template_id, cd.table_template_id, cd.mount_preset_id,
                       cd.memory_write_policy, cd.state_update_policy, cd.injection_policy,
                       cd.library_ids_json, cd.write_library_id, cd.auto_apply,
                       COUNT(conv.conversation_id) AS conversation_count,
                       MIN(conv.first_seen_at) AS first_seen_at,
                       MAX(conv.last_seen_at) AS last_seen_at
                FROM characters c
                LEFT JOIN character_defaults cd ON c.character_id = cd.character_id
                LEFT JOIN conversations conv ON c.character_id = conv.character_id
                GROUP BY c.character_id
                ORDER BY COALESCE(MAX(conv.last_seen_at), c.updated_at) DESC
            """)
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt)
                rows = [dict(r._mapping) for r in result.fetchall()]
            # 解析 JSON 字段
            for r in rows:
                r["aliases"] = json.loads(r.get("aliases_json") or "[]")
                if r.get("library_ids_json"):
                    r["library_ids"] = json.loads(r["library_ids_json"])
                r["auto_apply"] = bool(r["auto_apply"]) if r.get("auto_apply") is not None else None
                r["conversation_count"] = r.get("conversation_count") or 0
            return rows
        from app.storage.sqlite_app import list_characters
        return await list_characters(self._db_path)

    async def list_character_conversations(self, character_id: str) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("""
                        SELECT conversation_id, user_id, character_id, client_name, first_seen_at, last_seen_at
                        FROM conversations
                        WHERE character_id = :char_id
                        ORDER BY last_seen_at DESC
                    """),
                    {"char_id": character_id},
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_app import list_character_conversations
        return await list_character_conversations(self._db_path, character_id)

    async def list_conversations(self) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("""
                        SELECT conversation_id, user_id, character_id, client_name, last_seen_at, first_seen_at
                        FROM conversations
                        ORDER BY last_seen_at DESC
                        LIMIT 50
                    """)
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_app import list_conversations as _list_conv
        items, _total = await _list_conv(self._db_path)
        return items

    async def delete_conversation(self, conversation_id: str) -> bool:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("DELETE FROM conversations WHERE conversation_id = :conv_id"),
                    {"conv_id": conversation_id},
                )
                return result.rowcount > 0
        from app.storage.sqlite_app import delete_conversation
        return await delete_conversation(self._db_path, conversation_id)

    async def get_character_defaults(self, character_id: str) -> dict | None:
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT * FROM character_defaults WHERE character_id = :char_id"),
                    {"char_id": character_id},
                )
                row = result.fetchone()
                if not row:
                    return None
                row_dict = dict(row._mapping)
                return {
                    "character_id": row_dict["character_id"],
                    "profile_id": row_dict.get("profile_id"),
                    "template_id": row_dict.get("template_id"),
                    "table_template_id": row_dict.get("table_template_id"),
                    "mount_preset_id": row_dict.get("mount_preset_id"),
                    "memory_write_policy": row_dict.get("memory_write_policy"),
                    "state_update_policy": row_dict.get("state_update_policy"),
                    "injection_policy": row_dict.get("injection_policy"),
                    "library_ids": json.loads(row_dict.get("library_ids_json") or "[\"lib_default\"]"),
                    "write_library_id": row_dict.get("write_library_id"),
                    "auto_apply": bool(row_dict.get("auto_apply", 0)),
                }
        from app.storage.sqlite_app import get_character_defaults
        return await get_character_defaults(self._db_path, character_id)

    async def set_character_defaults(self, character_id: str, defaults: dict) -> None:
        if self._server_mode:
            from app.memory.conversation_policy import get_profile
            from sqlalchemy import text
            profile = get_profile(defaults.get("profile_id"))
            profile_id = defaults.get("profile_id") or profile.profile_id
            library_ids_json = json.dumps(defaults.get("library_ids", ["lib_default"]), ensure_ascii=False)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO character_defaults
                            (character_id, profile_id, template_id, table_template_id, mount_preset_id,
                             memory_write_policy, state_update_policy, injection_policy,
                             library_ids_json, write_library_id, auto_apply, created_at, updated_at)
                        VALUES (:char_id, :profile_id, :template_id, :table_template_id, :mount_preset_id,
                                :memory_write_policy, :state_update_policy, :injection_policy,
                                :library_ids_json, :write_library_id, :auto_apply, :now, :now)
                        ON CONFLICT(character_id) DO UPDATE SET
                            profile_id = EXCLUDED.profile_id,
                            template_id = EXCLUDED.template_id,
                            table_template_id = EXCLUDED.table_template_id,
                            mount_preset_id = EXCLUDED.mount_preset_id,
                            memory_write_policy = EXCLUDED.memory_write_policy,
                            state_update_policy = EXCLUDED.state_update_policy,
                            injection_policy = EXCLUDED.injection_policy,
                            library_ids_json = EXCLUDED.library_ids_json,
                            write_library_id = EXCLUDED.write_library_id,
                            auto_apply = EXCLUDED.auto_apply,
                            updated_at = EXCLUDED.updated_at
                    """),
                    {
                        "char_id": character_id,
                        "profile_id": profile_id,
                        "template_id": defaults.get("template_id", profile.template_id),
                        "table_template_id": defaults.get("table_template_id", profile.table_template_id),
                        "mount_preset_id": defaults.get("mount_preset_id", profile.mount_preset_id),
                        "memory_write_policy": defaults.get("memory_write_policy") or profile.memory_write_policy,
                        "state_update_policy": defaults.get("state_update_policy") or profile.state_update_policy,
                        "injection_policy": defaults.get("injection_policy") or profile.injection_policy,
                        "library_ids_json": library_ids_json,
                        "write_library_id": defaults.get("write_library_id"),
                        "auto_apply": int(defaults.get("auto_apply", True)),
                        "now": now,
                    },
                )
            return
        from app.storage.sqlite_app import set_character_defaults as _set_defaults
        await _set_defaults(
            self._db_path,
            character_id=character_id,
            profile_id=defaults.get("profile_id"),
            template_id=defaults.get("template_id"),
            table_template_id=defaults.get("table_template_id"),
            mount_preset_id=defaults.get("mount_preset_id"),
            memory_write_policy=defaults.get("memory_write_policy"),
            state_update_policy=defaults.get("state_update_policy"),
            injection_policy=defaults.get("injection_policy"),
            library_ids=defaults.get("library_ids"),
            write_library_id=defaults.get("write_library_id"),
            auto_apply=defaults.get("auto_apply", True),
        )

    async def update_character_profile(self, character_id: str, profile: dict) -> None:
        if self._server_mode:
            from sqlalchemy import text
            aliases_json = json.dumps(profile.get("aliases", []), ensure_ascii=False)
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("""
                        INSERT INTO characters (character_id, user_id, display_name, aliases_json, notes, source, created_at, updated_at)
                        VALUES (:char_id, :user_id, :display_name, :aliases_json, :notes, :source, :now, :now)
                        ON CONFLICT(character_id) DO UPDATE SET
                            display_name = EXCLUDED.display_name,
                            aliases_json = EXCLUDED.aliases_json,
                            notes = EXCLUDED.notes,
                            source = EXCLUDED.source,
                            updated_at = EXCLUDED.updated_at
                    """),
                    {
                        "char_id": character_id,
                        "user_id": profile.get("user_id", "default"),
                        "display_name": profile.get("display_name"),
                        "aliases_json": aliases_json,
                        "notes": profile.get("notes"),
                        "source": profile.get("source"),
                        "now": now,
                    },
                )
            return
        from app.storage.sqlite_app import update_character_profile as _update_profile
        await _update_profile(
            self._db_path,
            character_id=character_id,
            display_name=profile.get("display_name"),
            aliases=profile.get("aliases"),
            notes=profile.get("notes"),
            source=profile.get("source"),
            user_id=profile.get("user_id", "default"),
        )

    async def discover_characters(self) -> list[dict]:
        if self._server_mode:
            from sqlalchemy import text
            stmt = text("""
                SELECT c.character_id AS character_id, ch.display_name AS display_name,
                       ch.aliases_json AS aliases_json, ch.notes AS notes, ch.source AS source,
                       MAX(c.last_seen_at) AS last_seen_at, MIN(c.first_seen_at) AS first_seen_at,
                       COUNT(*) AS conversation_count,
                       cd.profile_id, cd.template_id, cd.table_template_id, cd.mount_preset_id,
                       cd.memory_write_policy, cd.state_update_policy, cd.injection_policy,
                       cd.library_ids_json, cd.write_library_id, cd.auto_apply
                FROM conversations c
                LEFT JOIN characters ch ON c.character_id = ch.character_id
                LEFT JOIN character_defaults cd ON c.character_id = cd.character_id
                WHERE c.character_id IS NOT NULL AND c.character_id != ''
                GROUP BY c.character_id
                ORDER BY MAX(c.last_seen_at) DESC
            """)
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt)
                rows = [dict(r._mapping) for r in result.fetchall()]
            result_list = []
            for r in rows:
                entry = {
                    "character_id": r["character_id"],
                    "display_name": r.get("display_name"),
                    "aliases": json.loads(r.get("aliases_json") or "[]"),
                    "notes": r.get("notes"),
                    "source": r.get("source"),
                    "conversation_count": r.get("conversation_count") or 0,
                    "first_seen_at": r.get("first_seen_at"),
                    "last_seen_at": r.get("last_seen_at"),
                    "profile_id": r.get("profile_id"),
                    "template_id": r.get("template_id"),
                    "table_template_id": r.get("table_template_id"),
                    "mount_preset_id": r.get("mount_preset_id"),
                    "memory_write_policy": r.get("memory_write_policy"),
                    "state_update_policy": r.get("state_update_policy"),
                    "injection_policy": r.get("injection_policy"),
                    "library_ids": json.loads(r["library_ids_json"]) if r.get("library_ids_json") else None,
                    "write_library_id": r.get("write_library_id"),
                    "auto_apply": bool(r["auto_apply"]) if r.get("auto_apply") is not None else None,
                }
                result_list.append(entry)
            return result_list
        from app.storage.sqlite_app import discover_characters
        return await discover_characters(self._db_path)

    # ========================================================================
    # State 数据访问
    # ========================================================================

    async def get_state_items(self, conversation_id: str) -> list[dict]:
        if self._server_mode:
            from app.storage.models import ConversationStateItem as StateItemModel
            from sqlalchemy import select
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(StateItemModel)
                    .where(
                        StateItemModel.conversation_id == conversation_id,
                        StateItemModel.resolved == 0,
                    )
                    .order_by(StateItemModel.updated_at.desc())
                )
                return [dict(r._mapping) for r in result.fetchall()]
        from app.storage.sqlite_state import SQLiteStateStore
        store = SQLiteStateStore(self._db_path)
        items, _total = await store.list_items(conversation_id)
        return [
            {
                "item_id": item.item_id,
                "conversation_id": item.conversation_id,
                "category": item.category,
                "item_key": item.item_key,
                "title": item.title,
                "content": item.content,
                "confidence": item.confidence,
                "status": item.status,
                "priority": item.priority,
                "source": item.source,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in items
        ]

    async def upsert_state_item(self, item: dict) -> str:
        if self._server_mode:
            from app.core.ids import generate_id
            from app.storage.models import ConversationStateItem as StateItemModel
            from sqlalchemy import select, update as sql_update, insert as sql_insert
            item_id = item.get("item_id") or generate_id("state_")
            now = datetime.now().isoformat()
            async with self._engine.begin() as conn:
                # 检查是否已有条目
                result = await conn.execute(
                    select(StateItemModel.item_id).where(
                        StateItemModel.conversation_id == item.get("conversation_id", ""),
                        StateItemModel.category == item.get("category", ""),
                        StateItemModel.item_key == item.get("item_key", ""),
                        StateItemModel.resolved == 0,
                    ).limit(1)
                )
                existing = result.fetchone()
                if existing:
                    existing_id = existing[0]
                    await conn.execute(
                        sql_update(StateItemModel)
                        .where(StateItemModel.item_id == existing_id)
                        .values(
                            title=item.get("title"),
                            content=item.get("content", ""),
                            confidence=item.get("confidence", 0.7),
                            updated_at=now,
                        )
                    )
                    return existing_id
                else:
                    await conn.execute(
                        sql_insert(StateItemModel).values(
                            item_id=item_id,
                            conversation_id=item.get("conversation_id", ""),
                            category=item.get("category", ""),
                            item_key=item.get("item_key"),
                            title=item.get("title"),
                            content=item.get("content", ""),
                            confidence=item.get("confidence", 0.7),
                            resolved=0,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    return item_id
        from app.storage.sqlite_state import SQLiteStateStore, ConversationStateItem
        store = SQLiteStateStore(self._db_path)
        state_item = ConversationStateItem(
            item_id=item.get("item_id"),
            conversation_id=item.get("conversation_id", ""),
            user_id=item.get("user_id"),
            character_id=item.get("character_id"),
            category=item.get("category", "general"),
            item_key=item.get("item_key"),
            title=item.get("title"),
            content=item.get("content", ""),
            confidence=item.get("confidence", 0.7),
            source=item.get("source", "manual"),
            priority=item.get("priority", 50),
            status=item.get("status", "active"),
        )
        return await store.upsert_item(state_item)

    async def delete_state_item(self, item_id: str) -> bool:
        if self._server_mode:
            from app.storage.models import ConversationStateItem as StateItemModel
            from sqlalchemy import update as sql_update, select
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    select(StateItemModel.item_id).where(StateItemModel.item_id == item_id).limit(1)
                )
                if not result.fetchone():
                    return False
                await conn.execute(
                    sql_update(StateItemModel)
                    .where(StateItemModel.item_id == item_id)
                    .values(resolved=1, updated_at=datetime.now().isoformat())
                )
                return True
        from app.storage.sqlite_state import SQLiteStateStore
        store = SQLiteStateStore(self._db_path)
        await store.delete_item(item_id)
        return True

    async def get_state_board_config(self, conversation_id: str) -> dict | None:
        """获取会话的状态板配置（ConversationConfig → dict）。"""
        if self._server_mode:
            from sqlalchemy import text
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT * FROM conversation_configs WHERE conversation_id = :conv_id"),
                    {"conv_id": conversation_id},
                )
                row = result.fetchone()
                if not row:
                    return None
                d = dict(row._mapping)
                d["created_from_default"] = bool(d.get("created_from_default", 0))
                return d
        from app.storage.sqlite_state import SQLiteStateStore
        store = SQLiteStateStore(self._db_path)
        config = await store.get_conversation_config(conversation_id)
        if config is None:
            return None
        return {
            "conversation_id": config.conversation_id,
            "profile_id": config.profile_id,
            "template_id": config.template_id,
            "table_template_id": config.table_template_id,
            "mount_preset_id": config.mount_preset_id,
            "memory_write_policy": config.memory_write_policy,
            "state_update_policy": config.state_update_policy,
            "injection_policy": config.injection_policy,
            "created_from_default": config.created_from_default,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

    async def init_state_db(self) -> None:
        if self._server_mode:
            # server 模式下由 SQLAlchemy ORM 管理 schema
            from app.storage.models import Base
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        from app.storage.sqlite_state import init_state_db as _init_state
        await _init_state(self._db_path)

    # ========================================================================
    # Vector 操作
    # ========================================================================

    async def get_vector_store(self) -> VectorStore | None:
        return self._vector_store

    def set_vector_store(self, store: VectorStore | None) -> None:
        self._vector_store = store


def get_repository() -> StorageRepository:
    global _repository
    if _repository is None:
        _repository = StorageRepository()
    return _repository


def reset_repository() -> None:
    global _repository
    _repository = None
