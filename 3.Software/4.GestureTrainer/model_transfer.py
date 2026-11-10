"""Model-only HTTP transfer. Never switches OTA mode or sends function commands."""
import http.client
import ipaddress
import json
import time
from model_package import decode


def upload(host,data,progress,*,http_port=80,wait_seconds=75,_allow_loopback=False):
    try:
        address=ipaddress.IPv4Address(host)
        if not address.is_private or address.is_unspecified or address.is_multicast or (address.is_loopback and not _allow_loopback):raise ValueError()
    except (ValueError,TypeError):raise ValueError('请填写魔杖的局域网地址。') from None
    host=str(address);package=decode(data);target=package['sha256']
    def info():
        connection=http.client.HTTPConnection(host,http_port,timeout=3)
        try:
            connection.request('GET','/api/model');r=connection.getresponse();body=r.read(16384)
            if r.status!=200:raise ValueError('未找到独立模型接口。请返回魔杖正常控制页；旧固件需要先安装一次6.0基础固件。')
            try:result=json.loads(body)
            except ValueError:raise ValueError('魔杖没有返回模型接口，请检查连接和基础固件版本。') from None
            if result.get('protocol')!=1 or result.get('contract')!=1:raise ValueError('模型协议不兼容，请更新基础固件或电脑工具。')
            return result
        finally:connection.close()
    progress('connecting',0,'正在检查魔杖的模型接口')
    initial=info()
    if initial.get('active_hash')==target:
        progress('complete',100,'魔杖已在使用这个模型，无需重复上传');return
    if not initial.get('storage_ready'):raise ValueError('魔杖没有可用的模型存储空间。')
    if initial.get('updating'):raise ValueError('魔杖正在处理另一次模型更新，请稍后重试。')
    if package['model_bytes']>initial.get('max_model_bytes',0) or len(package['gestures'])>initial.get('max_classes',0):raise ValueError('模型大小或手势数量超出这根魔杖的支持范围。')
    c=http.client.HTTPConnection(host,http_port,timeout=25)
    try:
        c.putrequest('POST','/api/model');c.putheader('Content-Type','application/octet-stream');c.putheader('X-Wand-Model','1');c.putheader('Content-Length',str(len(data)));c.endheaders()
        for offset in range(0,len(data),2048):
            chunk=data[offset:offset+2048];c.send(chunk);progress('uploading',min(65,int((offset+len(chunk))*65/len(data))),'正在上传手势模型')
        r=c.getresponse();body=r.read(16384)
        try:answer=json.loads(body)
        except ValueError:raise OSError('没有收到模型更新确认，请重连魔杖后检查。') from None
        if r.status!=202 or answer.get('accepted') is not True or type(answer.get('ticket')) is not int:raise OSError(answer.get('error','魔杖未接受模型，原固件未改动。'))
        ticket=answer['ticket']
    finally:c.close()
    deadline=time.monotonic()+wait_seconds
    progress('verifying',70,'正在等待魔杖校验和保存模型')
    while time.monotonic()<deadline:
        try:state=info()
        except (OSError,http.client.HTTPException):
            time.sleep(.5);continue
        if state.get('active_hash')==target:
            progress('complete',100,'新模型已生效，可以脱离电脑使用魔杖');return
        job=state.get('job',{})
        if job.get('ticket')==ticket:
            if job.get('phase')=='error':raise OSError(job.get('message','模型校验未通过，原模型仍保留。'))
            if job.get('phase')=='complete':progress('restarting',95,'模型已保存，等待魔杖重启；断开时请重连热点')
            else:progress('verifying',max(70,min(94,int(job.get('percent',0)))),job.get('message','正在保存模型'))
        time.sleep(.5)
    raise OSError('尚未确认新模型已生效，请重新连接魔杖后重试；重复上传会先检查当前模型。')
