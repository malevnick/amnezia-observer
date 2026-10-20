"""Generate secrets locally. Does not deploy or start any service."""
import getpass
import os
from pathlib import Path
import secrets
from werkzeug.security import generate_password_hash

if __name__=='__main__':
    target=Path('web.env')
    if target.exists():raise SystemExit('web.env already exists; refusing to overwrite')
    password=getpass.getpass('New panel password (at least 14 characters): ')
    if len(password)<14:raise SystemExit('Password too short')
    if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write('SECRET_KEY='+secrets.token_hex(32)+'\nADMIN_USER=admin\nADMIN_PASSWORD_HASH='+generate_password_hash(password)+'\nCOOKIE_SECURE=0\nPANEL_HOST=localhost\n')
    print('Created web.env with password hash. No service started.')
