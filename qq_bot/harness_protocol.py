"""Translate Harness Messages wire format to the site's budgeted Chat Completions."""
import json

def to_chat(body):
    messages=[]
    system=body.get('system',[])
    if isinstance(system,str):system=[{'type':'text','text':system}]
    if system:messages.append({'role':'system','content':'\n'.join(b.get('text','') for b in system)})
    for source in body['messages']:
        blocks=source.get('content',[])
        if isinstance(blocks,str):blocks=[{'type':'text','text':blocks}]
        text=[];reasoning=[];calls=[];results=[]
        for block in blocks:
            kind=block['type']
            if kind=='text':text.append(block['text'])
            elif kind=='thinking':reasoning.append(block['thinking'])
            elif kind=='tool_use':calls.append({'id':block['id'],'type':'function','function':{'name':block['name'],'arguments':json.dumps(block['input'],ensure_ascii=False)}})
            elif kind=='tool_result':results.append({'role':'tool','tool_call_id':block['tool_use_id'],'content':json.dumps(block.get('content',''),ensure_ascii=False)})
            else:raise ValueError('unsupported Harness block: '+kind)
        if text or calls or reasoning:
            msg={'role':source['role'],'content':'\n'.join(text)}
            if calls:msg['tool_calls']=calls
            if reasoning:msg['reasoning_content']='\n'.join(reasoning)
            messages.append(msg)
        messages.extend(results)
    return {'messages':messages,'tools':[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),'parameters':t['input_schema']}} for t in body.get('tools',[])],'max_tokens':body.get('max_tokens',4096)}

def events(result):
    choice=result['choices'][0];msg=choice['message'];usage=result.get('usage',{})
    yield 'message_start',{'type':'message_start','message':{'id':result.get('id','qq-harness'),'type':'message','role':'assistant','model':'deepseek-flash','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':usage.get('prompt_tokens',0),'output_tokens':0}}}
    blocks=[]
    if msg.get('reasoning_content'):blocks.append(('thinking',msg['reasoning_content']))
    if msg.get('content'):blocks.append(('text',msg['content']))
    for call in msg.get('tool_calls') or []:blocks.append(('tool_use',call))
    for index,(kind,value) in enumerate(blocks):
        if kind=='tool_use':
            start={'type':kind,'id':value['id'],'name':value['function']['name'],'input':{}}
            delta={'type':'input_json_delta','partial_json':value['function']['arguments']}
        else:
            start={'type':kind,kind:''};delta={'type':kind+'_delta',kind:value}
        yield 'content_block_start',{'type':'content_block_start','index':index,'content_block':start}
        yield 'content_block_delta',{'type':'content_block_delta','index':index,'delta':delta}
        yield 'content_block_stop',{'type':'content_block_stop','index':index}
    stop='tool_use' if msg.get('tool_calls') else ('max_tokens' if choice.get('finish_reason')=='length' else 'end_turn')
    yield 'message_delta',{'type':'message_delta','delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':usage.get('completion_tokens',0)}}
    yield 'message_stop',{'type':'message_stop'}
