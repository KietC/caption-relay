"""Read only delivery timestamps and counts; never export caption text or keys.

只读投递时间与数量，不导出字幕文字或密钥；必须符合用户对本机诊断的授权。
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import statistics


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def summary(values):
    if not values:
        return {'samples': 0}
    ordered = sorted(values)
    return {'samples': len(values), 'min_s': round(ordered[0], 3),
            'median_s': round(statistics.median(ordered), 3),
            'p95_s': round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 3),
            'max_s': round(ordered[-1], 3)}


def diagnose(root):
    now = datetime.now(timezone.utc)
    state = json.loads((root/'output/caption_viewer/viewer_status.json').read_text(encoding='utf-8-sig'))
    capture = json.loads((Path(state['capture_output'])/'status.json').read_text(encoding='utf-8-sig'))
    path = root/'mobile_caption/runtime/inbox.sqlite3'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3) as db:
        # Select aggregate/timing fields in read-only mode, never the caption body.
        # 只读查询汇总和时间字段，不查询字幕正文。
        db.execute('PRAGMA trusted_schema=OFF')
        rows = db.execute("SELECT received_at, json_extract(event,'$.timestamp'), json_extract(event,'$.type') FROM events ORDER BY id DESC LIMIT 2000").fetchall()
        total = db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    recent = [(timestamp(r), timestamp(t), kind) for r,t,kind in rows
              if timestamp(r) >= now-timedelta(minutes=2)]
    result = {'checked_at_utc': now.isoformat(), 'window_seconds': 120,
              'event_count_total': total, 'recent_event_types': dict(Counter(k for _,_,k in recent)),
              'capture_state': capture.get('state'),
              'last_event_age_s': round((now-timestamp(capture['last_event_at'])).total_seconds(),3) if capture.get('last_event_at') else None,
              'last_event_at': capture.get('last_event_at'),
              'last_snapshot_at': capture.get('last_snapshot_at'),
              'last_phone_timestamp': capture.get('last_phone_timestamp'),
              'original_count': capture.get('original_count'), 'translation_count': capture.get('translation_count'),
              'caption_freshness': capture.get('caption_freshness'),
              'snapshot_age_at_receive_s': capture.get('snapshot_age_at_receive_seconds'),
              'received_minus_phone_timestamp_s': summary([(r-t).total_seconds() for r,t,k in recent if k=='snapshot']),
              'note': 'Timestamp age includes phone queue, network and clock skew; excludes Xiaomi recognition time before the snapshot. This is not pure network latency.'}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=diagnose(args.root)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
