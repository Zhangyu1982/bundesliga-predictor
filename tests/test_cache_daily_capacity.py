"""每日缓存容量回归：同一天多个资源必须共存（曾因单列主键被后写覆盖）。"""
import sqlite3

from data_stores.cache import Cache


def test_two_resources_same_day_coexist(tmp_path):
    c = Cache(str(tmp_path / "cache.db"))
    c.set_daily("fdo:round:2627:BL1:10", {"matches": [1]})
    c.set_daily("fdo:standings:all", {"standings": [2]})
    assert c.get_daily("fdo:round:2627:BL1:10") == {"matches": [1]}
    assert c.get_daily("fdo:standings:all") == {"standings": [2]}


def test_legacy_single_pk_table_is_migrated_and_rows_kept(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cache (match_id TEXT PRIMARY KEY, key TEXT, payload TEXT, ts TEXT)")
    conn.execute("INSERT INTO cache VALUES (?,?,?,?)",
                 ("daily:2026-11-06", "fdo:round:BL1:9", '{"matches": [9]}', "t"))
    conn.commit()
    conn.close()

    c = Cache(str(db))
    # 旧数据仍可读
    assert c.get_daily("fdo:round:BL1:9", date="2026-11-06") == {"matches": [9]}
    # 迁移后同一天可以再写第二条资源而不覆盖旧条目
    c.set_daily("fdo:standings:all", {"standings": ["new"]}, date="2026-11-06")
    assert c.get_daily("fdo:round:BL1:9", date="2026-11-06") == {"matches": [9]}
    assert c.get_daily("fdo:standings:all", date="2026-11-06") == {"standings": ["new"]}


def test_repeated_write_same_key_replaces(tmp_path):
    c = Cache(str(tmp_path / "cache.db"))
    c.set_daily("fdo:round:2627:BL1:10", {"v": 1})
    c.set_daily("fdo:round:2627:BL1:10", {"v": 2})
    assert c.get_daily("fdo:round:2627:BL1:10") == {"v": 2}
