"""Read-only official account balance; never return credentials or raw errors."""
import os
from pathlib import Path
from datetime import datetime, timezone
import requests
from dotenv import dotenv_values

def balance():
    key=os.getenv('DEEPSEEK_API_KEY') or dotenv_values(Path(__file__).with_name('.env')).get('DEEPSEEK_API_KEY')
    if not key:raise ValueError('DeepSeek API Key 未配置')
    try:
        with requests.Session() as session:
            session.trust_env=False
            response=session.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key},timeout=(5,15),allow_redirects=False)
            if response.status_code!=200:raise ValueError('DeepSeek 余额查询 HTTP '+str(response.status_code))
            data=response.json()
    except requests.RequestException:
        raise ValueError('DeepSeek 余额接口连接失败，请稍后重试') from None
    return {'is_available':data.get('is_available'),'balance_infos':[
        {k:r.get(k) for k in ('currency','total_balance','granted_balance','topped_up_balance')}
        for r in data.get('balance_infos',[])], 'checked_at':datetime.now(timezone.utc).isoformat(),
        'scope':'API Key 所属账户余额，不是该 Key 单独额度，也不是消费明细'}
