"""只读客户端兼容性检查；不输出认证参数、玩家名或队友标识。"""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runtime_bootstrap
from lcu_api import ClientDiscovery,LCUClient,LCUError
from native import Windows
import time


def main():
    discovery=ClientDiscovery(Windows())
    connection=discovery.find(time.monotonic())
    if not connection:
        print(json.dumps(dict(status='unavailable',reason=discovery.reason,needs_admin=discovery.needs_admin,writes_sent=0,credentials_saved=False),ensure_ascii=False));return
    client=LCUClient(connection)
    capabilities=sorted(client.inspect());snapshot=client.snapshot()
    print(json.dumps(dict(status='read_only_checked',phase=snapshot.phase,capabilities=capabilities,
        lobby_member_count=len((snapshot.lobby or {}).get('members',[])),writes_sent=0,credentials_saved=False),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except LCUError as exc:print(json.dumps(dict(status='unavailable',reason=str(exc),writes_sent=0),ensure_ascii=False))
