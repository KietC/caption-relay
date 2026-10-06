"""Run an existing caption control entry point and report metadata only.

调用已有字幕控制入口，仅报告元数据；启停动作需符合用户当前授权。
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def run(root, executable, action):
    commands = {
        'viewer-stop': ['caption_viewer_control.py', 'stop'],
        'start-manual': ['mobile_caption_control.py', 'start', '--phone-configured'],
        'status': ['mobile_caption_control.py', 'status'],
    }
    process = subprocess.run([str(root/executable), *commands[action]], cwd=root,
                             capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=45,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'action': action, 'executable': executable, 'exit_code': process.returncode}
    if process.returncode:
        # Controllers return a short operational error, not caption payloads.
        # 控制器返回简短操作错误，不返回字幕内容。
        result['error'] = process.stderr.strip()[:1200]
    else:
        payload = json.loads(process.stdout) if process.stdout.strip() else {}
        viewer = payload.get('viewer', payload)
        capture = viewer.get('capture', {})
        result.update(tunnel_running=payload.get('tunnel_running'),
                      phone_paired=payload.get('phone_paired_to_current_endpoint'),
                      viewer_running=viewer.get('running'),
                      viewer_state=viewer.get('state'), source=viewer.get('source'),
                      capture_state=capture.get('state'),
                      capture_pid=viewer.get('capture_pid'),
                      capture_host_pid=capture.get('host_pid'),
                      last_event_at=capture.get('last_event_at'),
                      last_snapshot_at=capture.get('last_snapshot_at'),
                      original_count=capture.get('original_count'),
                      translation_count=capture.get('translation_count'))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['viewer-stop','start-manual','status'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--exe',default='CaptionRelayCaptions.exe')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=run(args.root,args.exe,args.action)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
    raise SystemExit(result['exit_code'])
