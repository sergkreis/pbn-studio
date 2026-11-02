// Research-only native graph adapter. Upstream compose emission pattern is
// MIT (c) 2024 TSANG, Hao Fung; see source-copy/LICENSE. No tracing algorithm fork.
use std::{fs, env, io::{Write, BufWriter}, path::Path, collections::BTreeMap};
use vtracer::{Config, Hierarchical, FitMode, ColorImage, CancelToken};
use vtracer::compose::Compositing;
use vtracer::mosaic::{LabelMap, BoundaryGraph, assemble, FittedGeom, OUTSIDE};
use vtracer::ir::{VectorDoc, Shape, MultiPath, SubPath, PathCmd};
fn opt(v: Option<u32>) -> String {v.map_or("null".into(), |n| n.to_string())}
fn write_u32(p: &Path, data: &[u32]) {let mut f=BufWriter::new(fs::File::create(p).unwrap());for n in data {f.write_all(&n.to_le_bytes()).unwrap();}}
fn emit(g: &FittedGeom, forward: bool, first: bool, s: &mut SubPath) {
    match g {
        FittedGeom::Polyline(ps) => { let p:Vec<_>=if forward {ps.clone()} else {ps.iter().rev().copied().collect()};
            if first {s.commands.push(PathCmd::MoveTo(p[0]));}
            for x in &p[1..] {s.commands.push(PathCmd::LineTo(*x));}
        },
        FittedGeom::Beziers(cs) => {let c:Vec<_>=if forward {cs.clone()} else {cs.iter().rev().map(|c|[c[3],c[2],c[1],c[0]]).collect()};
            if first {s.commands.push(PathCmd::MoveTo(c[0][0]));}
            for x in c {s.commands.push(PathCmd::CubicTo(x[1],x[2],x[3]));}
        }
    }
}
fn main() -> Result<(),Box<dyn std::error::Error>> {
    let a:Vec<String>=env::args().collect();
    if a.len()!=6 {return Err("usage: native_graph INPUT.rgba WIDTH HEIGHT OUTDIR pixel|recraft".into());}
    let w:usize=a[2].parse()?;let h:usize=a[3].parse()?;
    if w==0 || h==0 || w.checked_mul(h).filter(|n| *n<=40_000_000).is_none() {return Err("invalid dimensions/pixel budget".into());}
    let pixels=fs::read(&a[1])?;if pixels.len()!=w*h*4 {return Err("RGBA length mismatch".into());}
    if pixels.chunks_exact(4).any(|p| p[3]!=0 && p[3]!=255) {return Err("spike supports only opaque or binary alpha; explicit policy required".into());}
    let out=Path::new(&a[4]);fs::create_dir_all(out)?;
    let mut cfg=Config::default();cfg.hierarchical=Hierarchical::Cutout;
    match a[5].as_str() {
        "pixel" => {cfg.mode=FitMode::Pixel;cfg.filter_speckle=0;cfg.color_precision=8;cfg.layer_difference=0;cfg.optimize=0;cfg.path_precision=Some(8);},
        "recraft" => {cfg.max_colors=Some(16);cfg.simplify=Some(1.0);cfg.path_precision=Some(3);},
        _=>return Err("unknown preset".into())
    }
    fs::write(out.join("config.txt"),format!("{cfg:#?}"))?;
    let pipeline=cfg.build()?;
    let img=ColorImage{pixels,width:w,height:h};
    let start=std::time::Instant::now();
    let cached=pipeline.segment_with_progress(&img,&CancelToken::new(),&mut |p| eprintln!("progress {p:?}"))?;
    let segment_ms=start.elapsed().as_secs_f64()*1000.0;
    let mut seg=cached.clone();
    for fitter in &pipeline.color_fitters {fitter.fit(&mut seg);}
    let (fitter,merge_diff)=match &pipeline.compositing {Compositing::Mosaic{fitter,merge_diff}=>(fitter,*merge_diff),_=>unreachable!()};
    let mut map=LabelMap::from_segmentation(&seg);map.merge_similar(merge_diff);
    let graph=BoundaryGraph::extract(&map);let faces=assemble(&graph,&map);
    let fitted:Vec<_>=graph.segments.iter().map(|s| {let mut g=if s.is_ring(){fitter.fit_ring(s).geom}else{fitter.fit_open(s).geom};
        for pass in &pipeline.curve_passes {g=if s.is_ring(){pass.ring(g)}else{pass.open(g)};}g}).collect();
    // Separate PBN 4-connectivity; do not alter native 8-connected face grouping.
    let mut c4=vec![OUTSIDE;w*h];let mut comps=Vec::new();
    for i in 0..w*h {if c4[i]!=OUTSIDE || map.labels[i]==OUTSIDE {continue;}
        let id=comps.len() as u32;let r=map.labels[i];let mut stack=vec![i];c4[i]=id;
        let (mut area,mut x0,mut y0,mut x1,mut y1)=(0usize,w,h,0usize,0usize);
        while let Some(p)=stack.pop() {let x=p%w;let y=p/w;area+=1;x0=x0.min(x);y0=y0.min(y);x1=x1.max(x+1);y1=y1.max(y+1);
            for (nx,ny) in [(x as isize-1,y as isize),(x as isize+1,y as isize),(x as isize,y as isize-1),(x as isize,y as isize+1)] {
                if nx<0 || ny<0 || nx>=w as isize || ny>=h as isize {continue;}let n=ny as usize*w+nx as usize;
                if c4[n]==OUTSIDE && map.labels[n]==r {c4[n]=id;stack.push(n);}
            }
        }comps.push((r,area,[x0,y0,x1,y1]));
    }
    write_u32(&out.join("labels.u32le"),&map.labels);write_u32(&out.join("components4.u32le"),&c4);
    let mut f=BufWriter::new(fs::File::create(out.join("graph.json"))?);
    write!(f,"{{\"schema\":\"native-gate.graph/1\",\"width\":{w},\"height\":{h},\"outside\":{OUTSIDE},\"merge_diff\":{merge_diff},\"color_fitters\":{},\"curve_passes\":{},\"segment_ms\":{segment_ms},\"palette\":[",pipeline.color_fitters.len(),pipeline.curve_passes.len())?;
    for (i,p) in map.paints.iter().enumerate(){if i>0{write!(f,",")?;}let c=p.color();write!(f,"[{},{},{},{}]",c.r,c.g,c.b,c.a)?;}
    write!(f,"],\"components\":[")?;
    for (i,(r,n,b)) in comps.iter().enumerate(){if i>0{write!(f,",")?;}write!(f,"{{\"id\":{i},\"region\":{r},\"area\":{n},\"bbox\":{b:?}}}")?;}
    write!(f,"],\"nodes\":[")?;
    for (i,n) in graph.nodes.iter().enumerate(){if i>0{write!(f,",")?;}write!(f,"[{},{}]",n.corner.x,n.corner.y)?;}
    write!(f,"],\"edges\":[")?;
    let mut adj:BTreeMap<(u32,u32),(usize,Vec<usize>)>=BTreeMap::new();
    for (i,s) in graph.segments.iter().enumerate(){if i>0{write!(f,",")?;}
        write!(f,"{{\"id\":{i},\"left\":{},\"right\":{},\"start\":{},\"end\":{},\"ring\":{},\"contact\":{},\"lattice\":[",s.left,s.right,opt(s.start),opt(s.end),s.is_ring(),s.points.len()-1)?;
        for (j,p) in s.points.iter().enumerate(){if j>0{write!(f,",")?;}write!(f,"[{},{}]",p.x,p.y)?;}
        write!(f,"],\"kind\":\"{}\",\"geometry\":[",match &fitted[i]{FittedGeom::Polyline(_)=>"polyline",FittedGeom::Beziers(_)=>"cubic"})?;
        match &fitted[i] {
            FittedGeom::Polyline(ps)=>{for (j,p) in ps.iter().enumerate(){if j>0{write!(f,",")?;}write!(f,"[{:?},{:?}]",p.x,p.y)?;}},
            FittedGeom::Beziers(cs)=>{for (j,c) in cs.iter().enumerate(){if j>0{write!(f,",")?;}write!(f,"[")?;for (k,p) in c.iter().enumerate(){if k>0{write!(f,",")?;}write!(f,"[{:?},{:?}]",p.x,p.y)?;}write!(f,"]")?;}}
        }write!(f,"]}}")?;
        if s.left!=OUTSIDE && s.right!=OUTSIDE {let entry=adj.entry((s.left.min(s.right),s.left.max(s.right))).or_default();entry.0+=s.points.len()-1;entry.1.push(i);}
    }
    write!(f,"],\"faces8\":[")?;
    let mut doc=VectorDoc::new(w as u32,h as u32);
    for (i,face) in faces.iter().enumerate(){if i>0{write!(f,",")?;}write!(f,"{{\"id\":{i},\"region\":{},\"contours\":[",face.region)?;
        let mut path=MultiPath::new();
        for (j,c) in face.contours.iter().enumerate(){if j>0{write!(f,",")?;}write!(f,"[")?;let mut sub=SubPath::new();
            for (k,r) in c.0.iter().enumerate(){if k>0{write!(f,",")?;}write!(f,"[{},{}]",r.seg,r.forward)?;emit(&fitted[r.seg as usize],r.forward,k==0,&mut sub);}
            write!(f,"]")?;sub.commands.push(PathCmd::Close);path.push(sub);
        }write!(f,"]}}")?;doc.shapes.push(Shape{paint:map.paints[face.region as usize],path});
    }
    write!(f,"],\"adjacency\":[")?;
    for (i,((a,b),(n,ids))) in adj.iter().enumerate(){if i>0{write!(f,",")?;}write!(f,"{{\"a\":{a},\"b\":{b},\"contact\":{n},\"edges\":{ids:?}}}")?;}
    write!(f,"]}}")?;f.flush()?;
    // Keep authoritative graph before optimizers; compare graph-composed preview
    // with public finish(cached), which repeats the upstream downstream pipeline.
    for pass in &pipeline.optimizers {pass.run(&mut doc);}
    let svg=pipeline.writer.write(&doc);
    let upstream=pipeline.writer.write(&pipeline.finish(&cached)?);
    fs::write(out.join("preview.svg"),&svg)?;fs::write(out.join("upstream.svg"),&upstream)?;
    if svg!=upstream {return Err("graph preview differs from upstream finish".into());}
    println!("{{\"type\":\"done\",\"faces8\":{},\"components4\":{},\"edges\":{},\"elapsed_ms\":{},\"preview_upstream_identical\":true}}",faces.len(),comps.len(),graph.segments.len(),start.elapsed().as_secs_f64()*1000.0);
    Ok(())
}
