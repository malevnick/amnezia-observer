"""Explicit root-only recovery after stopping the collector; never restarts VPN."""
import argparse
from backend import Monitor, DockerDriver

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Run only after stopping amnezia-observer-broker')
    parser.add_argument('--unblock-all',action='store_true',required=True)
    parser.parse_args()
    m=Monitor(DockerDriver(),'/var/lib/amnezia-observer/blocked.json')
    peers,_,_=m.driver.snapshot()
    rows=m.accounting.view()
    for key in m.blocked | {k for k,r in rows.items() if r['enforced'] or r['limited']}:
        if key in peers:
            m.driver.restore(key,peers[key])
            _,_,live=m.driver.snapshot()
            if key not in live:raise SystemExit('Restore not confirmed; saved block list retained')
    m.blocked.clear();m.save()
    for key,row in rows.items():
        m.accounting.edit(key,row['alias'],0)
        m.accounting.enforcement(key,False)
    print('All remaining configured clients restored; manual blocks and quotas cleared. History retained.')
