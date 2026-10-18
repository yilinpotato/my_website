const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../about/profile.js'), 'utf8');
function page(storage = new Map(), href = 'https://potatoma.me/') {
  const element = () => ({events:{}, addEventListener(name, fn){this.events[name]=fn;}, focus(){}});
  const about = element(), projects = element(), entry = element();
  const chooser = Object.assign(element(), {open:false, showModal(){this.open=true;}, close(){this.open=false;}, querySelector(){return about;}});
  const location = {href};
  const events = {};
  vm.runInNewContext(source, {
    URL, location,
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    history:{pushState(_,__,url){location.href=String(url);}},
    addEventListener:(name,fn)=>events[name]=fn,
    document:{documentElement:{lang:'zh-CN',dataset:{}},body:{classList:{contains:()=>false,add(){},remove(){}}},
      querySelectorAll:selector=>selector==='dialog'?[chooser]:[],addEventListener(){},
      getElementById:id=>({'entry-dialog':chooser,'choose-projects':projects,'about-entry':entry}[id])},
  });
  return {chooser,projects,about,events,location,storage};
}
const click = el => el.events.click({preventDefault(){}});
test('three consecutive modal choices persist across visits; fourth visit skips dialog',()=>{
  const storage=new Map();
  for(let i=1;i<=3;i++){
    const p=page(storage);assert.equal(p.chooser.open,true);click(p.projects);
    assert.equal(p.chooser.open,false);assert.equal(storage.get('site-project-choice-streak'),String(i));
    p.events.pageshow();assert.equal(storage.get('site-project-choice-streak'),String(i));
  }
  assert.equal(page(storage).chooser.open,false);
});
test('choosing About interrupts the consecutive sequence',()=>{
  const storage=new Map([['site-project-choice-streak','2']]);
  click(page(storage).about);
  const p=page(storage);click(p.projects);
  assert.equal(storage.get('site-project-choice-streak'),'1');
});
test('direct projects URL does not count, and Escape counts as choosing projects',()=>{
  const storage=new Map();
  assert.equal(page(storage,'https://potatoma.me/?view=projects').chooser.open,false);
  assert.equal(storage.size,0);
  const p=page(storage);p.chooser.events.cancel({preventDefault(){}});
  assert.equal(storage.get('site-project-choice-streak'),'1');
  assert.equal(p.chooser.open,false);
});
