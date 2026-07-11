/**
 * aero3d.js — Аэро-панорама из OSM + планы этажей
 * Камера: FIRST-PERSON (как Pannellum)
 */
(function(){
"use strict";

class AeroRenderer{
  constructor(el,opts){
    this.el=el;
    this.o=Object.assign({bg:0x87CEEB,ground:0x5a7247,fov:75},opts||{});
    this.scene=this.cam=this.ren=this.group=this.roadGroup=this.treeGroup=this.waterGroup=this.ground=null;
    this.raf=null; this.dead=false;
    this.yaw=0; this.pitch=-20;
    this._dragging=false; this._lastMX=0; this._lastMY=0;
    this._clickStartX=0; this._clickStartY=0;
    this._resize=this._resize.bind(this); this._frame=this._frame.bind(this);
    // Plans + building click
    this._buildings=[];
    this._plans=[];
    this._raycaster=null;
    this._mouse=new THREE.Vector2();
    this.onBuildingClick=null;
  }

  init(){
    var T=window.THREE; if(!T)return false; this.T=T;
    this._raycaster=new T.Raycaster();
    this.scene=new T.Scene();
    this.scene.background=new T.Color(this.o.bg);
    this.scene.fog=new T.FogExp2(0xc8dae8,0.001);
    var w=this.el.clientWidth,h=this.el.clientHeight;
    this.cam=new T.PerspectiveCamera(this.o.fov,w/Math.max(1,h),0.5,50000);
    this.ren=new T.WebGLRenderer({antialias:true,powerPreference:"high-performance"});
    this.ren.setSize(w,h); this.ren.setPixelRatio(Math.min(devicePixelRatio,2));
    this.ren.shadowMap.enabled=true; this.ren.shadowMap.type=T.PCFSoftShadowMap;
    this.ren.toneMapping=T.ACESFilmicToneMapping; this.ren.toneMappingExposure=1.1;
    this.el.appendChild(this.ren.domElement);

    var self=this;
    this.el.style.cursor="grab";
    this.el.addEventListener("pointerdown",function(e){
      self._dragging=true; self._lastMX=e.clientX; self._lastMY=e.clientY;
      self._clickStartX=e.clientX; self._clickStartY=e.clientY;
      self.el.style.cursor="grabbing"; e.preventDefault();
    });
    window.addEventListener("pointermove",function(e){
      if(!self._dragging)return;
      var dx=e.clientX-self._lastMX, dy=e.clientY-self._lastMY;
      self._lastMX=e.clientX; self._lastMY=e.clientY;
      self.yaw-=dx*0.15;
      self.pitch+=dy*0.15;
      self.pitch=Math.max(-85,Math.min(10,self.pitch));
    });
    window.addEventListener("pointerup",function(e){
      if(!self._dragging)return;
      self._dragging=false; self.el.style.cursor="grab";
      // ★ Клик по зданию (если мышь не двигалась)
      var moved=Math.abs(e.clientX-self._clickStartX)+Math.abs(e.clientY-self._clickStartY);
      if(moved<5) self._handleClick(e);
    });

    this.scene.add(new T.AmbientLight(0xffffff,0.6));
    var h2=new T.HemisphereLight(0x87CEEB,0x5a7247,0.7); h2.position.set(0,500,0); this.scene.add(h2);
    var s=new T.DirectionalLight(0xfff8e8,1.0); s.position.set(200,500,150);
    s.castShadow=true; s.shadow.mapSize.set(2048,2048);
    var sc=s.shadow.camera; sc.near=1;sc.far=3000;sc.left=sc.bottom=-600;sc.right=sc.top=600;
    this.scene.add(s);
    var f=new T.DirectionalLight(0xFFF0E0,0.25); f.position.set(-200,400,-100); this.scene.add(f);

    window.addEventListener("resize",this._resize); return true;
  }

  _handleClick(e){
    if(!this._raycaster||!this.cam||!this.group)return;
    var rect=this.el.getBoundingClientRect();
    this._mouse.x=((e.clientX-rect.left)/rect.width)*2-1;
    this._mouse.y=-((e.clientY-rect.top)/rect.height)*2+1;
    this._raycaster.setFromCamera(this._mouse,this.cam);
    var hits=this._raycaster.intersectObjects(this.group.children,true);
    if(hits.length>0){
      // Ищем родительскую группу здания
      var obj=hits[0].object;
      while(obj.parent&&obj.parent!==this.group) obj=obj.parent;
      if(obj.userData&&obj.userData.buildingIndex!=null){
        var bData=this._buildings[obj.userData.buildingIndex];
        if(bData&&this.onBuildingClick) this.onBuildingClick(bData);
      }
    }
  }

  load(data, camX, camZ, camY){
    var T=this.T, blds=data.buildings||[], rds=data.roads||[], trs=data.trees||[], wtrs=data.waters||[], r=data.radius_m||500;
    camX=camX||0; camZ=camZ||0; camY=camY||80;
    this._camPos={x:camX,y:camY,z:camZ};
    this._buildings=blds;
    this._plans=data.plans||[];
    this.cam.position.set(camX, camY, camZ);

    // Земля
    if(this.ground){this.scene.remove(this.ground);this.ground.geometry.dispose();this.ground.material.dispose();}
    var sz=Math.max(r*3,3000);
    this.ground=new T.Mesh(new T.PlaneGeometry(sz,sz),new T.MeshStandardMaterial({color:this.o.ground,roughness:0.95}));
    this.ground.rotation.x=-Math.PI/2;this.ground.position.y=-0.05;this.ground.receiveShadow=true;this.scene.add(this.ground);

    // Дороги
    if(this.roadGroup){this.scene.remove(this.roadGroup);this.roadGroup.traverse(function(c){if(c.geometry)c.geometry.dispose();if(c.material)c.material.dispose();});}
    this.roadGroup=new T.Group();
    for(var ri=0;ri<rds.length;ri++){
      var rd=rds[ri]; if(rd.path.length<2)continue;
      var halfW=rd.width*0.5;
      var verts=[],idxs=[];
      for(var pi=0;pi<rd.path.length;pi++){
        var p0=rd.path[pi]; var nx=0,nz=1;
        if(pi<rd.path.length-1){var p1=rd.path[pi+1];var dx=p1[0]-p0[0],dz=p1[1]-p0[1];var len=Math.sqrt(dx*dx+dz*dz)||1;nx=-dz/len;nz=dx/len;}
        else if(pi>0){var pm=rd.path[pi-1];var dx2=p0[0]-pm[0],dz2=p0[1]-pm[1];var len2=Math.sqrt(dx2*dx2+dz2*dz2)||1;nx=-dz2/len2;nz=dx2/len2;}
        verts.push(p0[0]+nx*halfW,0.03,p0[1]+nz*halfW);
        verts.push(p0[0]-nx*halfW,0.03,p0[1]-nz*halfW);
        if(pi>0){var bi=(pi-1)*2;idxs.push(bi,bi+1,bi+2,bi+1,bi+3,bi+2);}
      }
      var geo=new T.BufferGeometry();geo.setAttribute("position",new T.Float32BufferAttribute(verts,3));geo.setIndex(idxs);geo.computeVertexNormals();
      var mat=new T.MeshStandardMaterial({color:new T.Color(rd.color),roughness:0.9,side:T.DoubleSide});
      var mesh=new T.Mesh(geo,mat);mesh.receiveShadow=true;this.roadGroup.add(mesh);
    }
    this.scene.add(this.roadGroup);

    // Водоёмы
    if(this.waterGroup){this.scene.remove(this.waterGroup);this.waterGroup.traverse(function(c){if(c.geometry)c.geometry.dispose();if(c.material)c.material.dispose();});}
    this.waterGroup=new T.Group();
    var waterMat=new T.MeshStandardMaterial({color:0x4a90d9,roughness:0.3,metalness:0.1,transparent:true,opacity:0.85,side:T.DoubleSide});
    for(var wi=0;wi<wtrs.length;wi++){var wr=wtrs[wi];if(wr.length<3)continue;try{var wShape=new T.Shape();wShape.moveTo(wr[0][0],wr[0][1]);for(var wj=1;wj<wr.length;wj++)wShape.lineTo(wr[wj][0],wr[wj][1]);wShape.closePath();var wGeo=new T.ShapeGeometry(wShape);wGeo.rotateX(-Math.PI/2);var wMesh=new T.Mesh(wGeo,waterMat);wMesh.position.y=0.01;this.waterGroup.add(wMesh);}catch(e){}}
    this.scene.add(this.waterGroup);

    // Здания + планы на крышах
    if(this.group){this.scene.remove(this.group);this.group.traverse(function(c){if(c.geometry)c.geometry.dispose();if(c.material)(Array.isArray(c.material)?c.material:[c.material]).forEach(function(m){m.dispose();});});}
    this.group=new T.Group();var cnt=0;
    for(var i=0;i<blds.length&&cnt<3000;i++){
      var b=blds[i],ring=b.ring,h=b.height; if(!ring||ring.length<3||!h)continue;
      try{
        var sh=new T.Shape();sh.moveTo(ring[0][0],ring[0][1]);
        for(var j=1;j<ring.length;j++)sh.lineTo(ring[j][0],ring[j][1]);sh.closePath();
        var ext=new T.ExtrudeGeometry(sh,{depth:h,bevelEnabled:false});ext.rotateX(-Math.PI/2);
        var col=new T.Color(b.color);
        var w=new T.Mesh(ext,new T.MeshStandardMaterial({color:col,roughness:0.7,metalness:0.05}));
        w.castShadow=w.receiveShadow=true;
        var rg=new T.ShapeGeometry(sh);rg.rotateX(-Math.PI/2);

        // Крыша: с планом или без
        var roofMat;
        if(b.plan){
          // ★ План на крыше — загружаем текстуру
          roofMat=new T.MeshStandardMaterial({color:0xffffff,roughness:0.5,metalness:0.0});
          var loader=new T.TextureLoader();
          loader.load("/plans/"+b.plan,function(m){return function(tex){
            tex.encoding=T.sRGBEncoding;
            m.map=tex; m.needsUpdate=true;
          };}(roofMat));
        }else{
          roofMat=new T.MeshStandardMaterial({color:col.clone().multiplyScalar(0.8),roughness:0.85});
        }
        var rf=new T.Mesh(rg,roofMat);
        rf.position.y=h+0.01;rf.castShadow=true;

        var g=new T.Group();g.add(w);g.add(rf);
        g.userData={buildingIndex:i}; // ★ для raycasting
        this.group.add(g);cnt++;
      }catch(e){}
    }
    this.scene.add(this.group);

    // Деревья
    if(this.treeGroup){this.scene.remove(this.treeGroup);this.treeGroup.traverse(function(c){if(c.geometry)c.geometry.dispose();if(c.material)c.material.dispose();});}
    this.treeGroup=new T.Group();
    var trunkMat=new T.MeshStandardMaterial({color:0x8B6914,roughness:0.9});
    var leafColors=[0x2d5a2d,0x3a7a3a,0x4a8a4a,0x2a6a2a,0x5a9a3a];
    for(var ti=0;ti<trs.length&&ti<800;ti++){
      var t=trs[ti],th=8+Math.random()*12,rw=3+Math.random()*5;
      var trunk=new T.Mesh(new T.CylinderGeometry(0.2,0.35,th*0.4,5),trunkMat);
      trunk.position.set(t.x,th*0.2,t.y);trunk.castShadow=true;
      var leafMat=new T.MeshStandardMaterial({color:leafColors[ti%leafColors.length],roughness:0.8});
      var crown=new T.Mesh(new T.ConeGeometry(rw,th*0.7,6),leafMat);
      crown.position.set(t.x,th*0.65,t.y);crown.castShadow=true;
      var tree=new T.Group();tree.add(trunk);tree.add(crown);this.treeGroup.add(tree);
    }
    this.scene.add(this.treeGroup);
  }

  start(){this._frame();}
  _frame(){
    if(this.dead)return;this.raf=requestAnimationFrame(this._frame);
    if(!this._dragging)this.yaw+=0.02;
    var yawRad=this.yaw*Math.PI/180,pitchRad=this.pitch*Math.PI/180;
    var lookDir=new this.T.Vector3(Math.sin(yawRad)*Math.cos(pitchRad),Math.sin(pitchRad),-Math.cos(yawRad)*Math.cos(pitchRad));
    var p=this._camPos;
    this.cam.position.set(p.x,p.y,p.z);
    this.cam.lookAt(p.x+lookDir.x,p.y+lookDir.y,p.z+lookDir.z);
    this.ren.render(this.scene,this.cam);
  }
  getYaw(){return this.yaw;}getPitch(){return this.pitch;}getHfov(){if(!this.cam)return 75;var w=this.el.clientWidth,h=this.el.clientHeight,vfov=this.cam.fov*Math.PI/180;return 2*Math.atan(Math.tan(vfov/2)*(w/h))*180/Math.PI;}
  setYaw(deg,dur){var start=this.yaw,target=deg,t0=performance.now(),d=dur||700,self=this;(function a(){var t=Math.min(1,(performance.now()-t0)/d),e=t*(2-t);self.yaw=start+(target-start)*e;if(t<1)requestAnimationFrame(a);})();}
  _resize(){if(this.dead)return;var w=this.el.clientWidth,h=this.el.clientHeight;this.cam.aspect=w/Math.max(1,h);this.cam.updateProjectionMatrix();this.ren.setSize(w,h);}
  destroy(){
    this.dead=true;if(this.raf)cancelAnimationFrame(this.raf);window.removeEventListener("resize",this._resize);
    [this.group,this.roadGroup,this.treeGroup,this.waterGroup].forEach(function(g){if(g){this.scene.remove(g);g.traverse(function(c){if(c.geometry)c.geometry.dispose();if(c.material)(Array.isArray(c.material)?c.material:[c.material]).forEach(function(m){m.dispose();});});}}.bind(this));
    if(this.ground){this.scene.remove(this.ground);this.ground.geometry.dispose();this.ground.material.dispose();}
    if(this.ren){this.ren.dispose();if(this.ren.domElement.parentNode)this.ren.domElement.parentNode.removeChild(this.ren.domElement);}
    this.scene=this.cam=this.ren=null;
  }
}

window.AeroRenderer=AeroRenderer;
window.loadAero=async function(id,lat,lon,opts){
  var el=document.getElementById(id);if(!el)return null;
  el.innerHTML='<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:#ccc;gap:14px"><div style="width:36px;height:36px;border:3px solid rgba(255,255,255,.1);border-top-color:#4d8dff;border-radius:50%;animation:spin .8s linear infinite"></div><p>Загрузка зданий из OSM…</p></div>';
  var r=(opts&&opts.radius)||300,data;
  try{var res=await fetch("/api/osm-buildings?lat="+encodeURIComponent(lat)+"&lon="+encodeURIComponent(lon)+"&radius_m="+r);data=await res.json();if(data.status==="error")throw new Error(data.message);}
  catch(e){el.innerHTML='<p style="color:#f66;padding:20px">Ошибка: '+e.message+'</p>';return null;}
  el.innerHTML="";var ar=new AeroRenderer(el,opts);
  if(!ar.init()){el.innerHTML='<p style="color:#f66;padding:20px">WebGL не поддерживается</p>';return null;}
  ar.load(data,opts&&opts.camX,opts&&opts.camZ,opts&&opts.camY);ar.start();return ar;
};
})();
