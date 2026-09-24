from dataclasses import dataclass
from datetime import datetime

from auth.tokens import Claims
from database import db_cursor

ROLES = ("student", "faculty", "admin")
ACTIONS = ("generate", "upload", "syllabus")

_USER_COLUMNS = "id, ms_tid, ms_oid, email, name, role, verified, created_at"
_TODAY = "(now() AT TIME ZONE 'UTC')::date"


@dataclass(frozen=True)
class User:
    id: int
    ms_tid: str
    ms_oid: str
    email: str | None
    name: str | None
    role: str | None
    verified: bool
    created_at: datetime | None = None


def user_summary(user: User, used_today: dict[str, int]) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "verified": user.verified,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "usedToday": used_today,
    }


class PostgresUserStore:
    """Users and daily usage counters in Postgres."""

    def get_or_create(self, claims: Claims) -> User:
        select = f"SELECT {_USER_COLUMNS} FROM users WHERE ms_tid = %s AND ms_oid = %s"
        with db_cursor() as cur:
            cur.execute(select, (claims.tid, claims.oid))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO users (ms_tid, ms_oid, email, name) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ms_tid, ms_oid) DO NOTHING",
                    (claims.tid, claims.oid, claims.email, claims.name),
                )
                cur.execute(select, (claims.tid, claims.oid))
                row = cur.fetchone()
        return User(*row)

    def get(self, user_id: int) -> User | None:
        with db_cursor() as cur:
            cur.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return User(*row) if row else None

    def touch(self, user_id: int) -> None:
        with db_cursor() as cur:
            cur.execute("UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))

    def set_role_once(self, user_id: int, role: str) -> bool:
        with db_cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE id = %s AND role IS NULL RETURNING id", (role, user_id))
            return cur.fetchone() is not None

    def update(self, user_id: int, role: str | None = None, verified: bool | None = None) -> User | None:
        assignments, params = [], []
        if role is not None:
            assignments.append("role = %s")
            params.append(role)
        if verified is not None:
            assignments.append("verified = %s")
            params.append(verified)
        if not assignments:
            return self.get(user_id)
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(assignments)} WHERE id = %s RETURNING {_USER_COLUMNS}",
                (*params, user_id),
            )
            row = cur.fetchone()
        return User(*row) if row else None

    def find_by_email(self, email: str) -> list[User]:
        with db_cursor() as cur:
            cur.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE lower(email) = lower(%s) ORDER BY created_at",
                (email,),
            )
            return [User(*row) for row in cur.fetchall()]

    def list_with_usage(self) -> list[dict]:
        with db_cursor() as cur:
            cur.execute(f"""
                SELECT u.id, u.ms_tid, u.ms_oid, u.email, u.name, u.role, u.verified, u.created_at,
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'generate'), 0),
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'upload'), 0),
                       COALESCE(SUM(d.count) FILTER (WHERE d.action = 'syllabus'), 0)
                FROM users u
                LEFT JOIN usage_daily d ON d.subject = 'user:' || u.id AND d.day = {_TODAY}
                GROUP BY u.id
                ORDER BY u.created_at DESC
            """)
            rows = cur.fetchall()
        return [
            user_summary(User(*row[:8]), {"generate": int(row[8]), "upload": int(row[9]), "syllabus": int(row[10])})
            for row in rows
        ]

    def consume_quota(self, subject: str, action: str, limit: int) -> bool:
        """Atomically counts one use; False (and nothing counted) once the limit is reached."""
        with db_cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO usage_daily (subject, action, day, count)
                VALUES (%(subject)s, %(action)s, {_TODAY}, 1)
                ON CONFLICT (subject, action, day)
                DO UPDATE SET count = usage_daily.count + 1
                WHERE usage_daily.count < %(limit)s
                RETURNING count
                """,
                {"subject": subject, "action": action, "limit": limit},
            )
            return cur.fetchone() is not None

    def refund_quota(self, subject: str, action: str) -> None:
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE usage_daily SET count = count - 1 "
                f"WHERE subject = %s AND action = %s AND day = {_TODAY} AND count > 0",
                (subject, action),
            )

    def usage_today(self, subject: str) -> dict[str, int]:
        used = {action: 0 for action in ACTIONS}
        with db_cursor() as cur:
            cur.execute(f"SELECT action, count FROM usage_daily WHERE subject = %s AND day = {_TODAY}", (subject,))
            for action, count in cur.fetchall():
                used[action] = count
        return used
