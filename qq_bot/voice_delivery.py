"""Send a locally prepared song as a QQ voice message and verify its receipt."""
import base64
from pathlib import Path


async def send_voice(call, event, path, as_file=False):
    audio = Path(path).read_bytes()
    if not 1000 <= len(audio) <= 16 * 1024 * 1024:
        raise ValueError('语音文件大小异常')
    expected='file' if as_file else 'record'
    data={'file':'base64://'+base64.b64encode(audio).decode()}
    if as_file:data['name']='大肥鱼-完整歌曲.mp3'
    else:data['magic']=False
    params={'message':[{'type':expected,'data':data}]}
    if event['message_type'] == 'private':
        method = 'send_private_msg'
        params['user_id'] = int(event['user_id'])
    elif event['message_type'] == 'group':
        method = 'send_group_msg'
        params['group_id'] = int(event['group_id'])
    else:
        raise ValueError('不支持的语音会话类型')
    receipt = await call(method, params)
    message_id = (receipt or {}).get('message_id')
    if not message_id:
        raise RuntimeError('QQ 没有返回语音消息编号')
    stored = await call('get_msg', {'message_id': message_id})
    if not any(segment.get('type') == expected for segment in stored.get('message', [])
               if isinstance(segment, dict)):
        raise RuntimeError('QQ 回执中未找到语音消息；请勿自动重发')
    return receipt
