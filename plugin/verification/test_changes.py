import ast, importlib.util, pathlib, types, unittest
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parent.parent

def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
old=load(ROOT/'verification/baseline_pixelops.py','old_pixels')
new=load(ROOT/'ps_tools/pixelops.py','new_pixels')
luts=load(ROOT/'ps_tools/lutfile.py','luts')

class PixelTests(unittest.TestCase):
 def test_exact_numpy_results(self):
  rng=np.random.default_rng(532)
  from array import array
  lut3=luts.Lut(3,array('f',rng.random(81)))
  lut1=luts.Lut(0,None,data1d=array('f',rng.random(12)),size1d=4)
  shaper=luts.Lut(3,lut3.data,data1d=array('f',[0,0,0,.4,.5,.6,1,1,1]),size1d=3)
  new.NUMPY_CHUNK_PIXELS=127
  for depth,dtype in [('U8',np.uint8),('U16',np.uint16),('F32',np.float32)]:
   size,pos=new.layout('RGBA',depth)
   arr=(rng.random((1025,4))*(1 if size==4 else (255 if size==1 else 65535))).astype(dtype)
   arr[::7,3]=0
   for lut in [lut3,lut1,shaper]:
    for linear in [False,True]:
     a=bytearray(arr.tobytes());b=bytearray(a)
     old._lut_numpy(np,a,size,pos,lut,linear);new._lut_numpy(np,b,size,pos,lut,linear)
     self.assertEqual(a,b,(depth,lut.size,linear))
   for relative in [False,True]:
    adj=list(rng.uniform(-1,1,36));a=bytearray(arr.tobytes());b=bytearray(a)
    old._sel_numpy(np,a,size,pos,adj,relative);new._sel_numpy(np,b,size,pos,adj,relative)
    self.assertEqual(a,b,(depth,relative))
 def test_chunk_boundaries(self):
  for size in [1,2,4]:
   raw=bytearray(257*4*size)
   chunks=list(new._chunks(raw,size))
   self.assertEqual(sum(map(len,chunks)),len(raw))
   self.assertTrue(all(len(c)%(4*size)==0 for c in chunks))

class Timer:
 def __init__(self):self.active=False;self.starts=[]
 def isActive(self):return self.active
 def start(self,ms):self.active=True;self.starts.append(ms)
 def stop(self):self.active=False

# Execute the actual scheduler methods with lightweight test doubles; no Qt
# installation or Krita document mutation is needed.
def scheduler_class():
 tree=ast.parse((ROOT/'ps_tools/props_docker.py').read_text())
 cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef) and m.name=='_apply_pending' for m in n.body))
 methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ['_edited','_apply_pending']]
 mod=ast.Module(body=[ast.ClassDef(name='Scheduler',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
 ns={'A':types.SimpleNamespace(BAKED_KINDS=('baked',)), 'core':types.SimpleNamespace()};exec(compile(ast.fix_missing_locations(mod),'<actual scheduler>','exec'),ns)
 return ns['Scheduler']

class SchedulingTests(unittest.TestCase):
 def make(self):
  s=scheduler_class()();s.kind='baked';s.node='layer A';s.doc='document A';s._applying=False;s.pending=None;s.apply_timer=Timer()
  ed=types.SimpleNamespace(params=lambda:{'v':self.value});self.value=1
  s.editors={'baked':ed};s.sender=lambda:ed
  return s
 def test_continuous_drag_keeps_deadline_and_latest_value(self):
  s=self.make()
  for self.value in range(100):s._edited()
  self.assertEqual(s.apply_timer.starts,[120]);self.assertEqual(s.pending,{'v':99})
  self.assertEqual(s._pending_target,('layer A','document A','baked'))
 def test_edit_during_apply_does_not_reenter(self):
  s=self.make();s._edited();calls=[]
  def work():
   calls.append(1);s.pending=None;self.value=42;s._edited();s._apply_pending()
  s._apply_pending_now=work;s._apply_pending()
  self.assertEqual(calls,[1]);self.assertEqual(s.pending,{'v':42});self.assertTrue(s.apply_timer.active)
 def test_guard_recovers_from_exception(self):
  s=self.make();s._edited()
  def fail():s.pending=None;raise ValueError('test')
  s._apply_pending_now=fail
  with self.assertRaises(ValueError):s._apply_pending()
  self.assertFalse(s._applying)

class CacheTests(unittest.TestCase):
 def test_auto_render_does_not_retain_full_image(self):
  tree=ast.parse((ROOT/'ps_tools/baked.py').read_text())
  selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render']
  class Node:
   def setPixelData(self,*args):pass
   def locked(self):return True
  class Doc:
   def width(self):return 2
   def height(self):return 2
   def colorModel(self):return 'RGBA'
   def colorDepth(self):return 'U8'
  ns={'core':types.SimpleNamespace(node_key=lambda n:'node',bytes_per_pixel=lambda *a:4,
       refresh=lambda d:None,find_blendif_mask=lambda n:None),
      '_under_cache':{},'CACHE_LIMIT':16,'is_neutral':lambda *a:False,
      'capture_under_raw':lambda *a,**k:bytes(16),'compute':lambda *a:bytearray(16)}
  exec(compile(ast.Module(body=selected,type_ignores=[]),'<actual render>','exec'),ns)
  render=ns['render'];cache=ns['_under_cache'];doc=Doc();node=Node()
  render(doc,node,'selective_color',{},use_cache=False);self.assertFalse(cache)
  render(doc,node,'selective_color',{},use_cache=True);self.assertEqual(len(cache),1)
  render(doc,node,'selective_color',{},use_cache=False);self.assertFalse(cache)
  ns['CACHE_LIMIT']=15
  render(doc,node,'selective_color',{},use_cache=True);self.assertFalse(cache)

if __name__=='__main__':unittest.main(verbosity=2)
