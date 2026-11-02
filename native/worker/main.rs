// SPDX-License-Identifier: GPL-3.0-only
// PBN Studio CLI worker adapter. Upstream compose emission pattern is
// MIT (c) 2024 TSANG, Hao Fung; see source-copy/LICENSE. No tracing algorithm fork.
use std::{fs, env, io::{Write, BufWriter}, path::Path, collections::BTreeMap};
use vtracer::{Config, Hierarchical, ColorImage, CancelToken};
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

// Source-local valley detector + 4-connected components. Opposing samples
// reject ordinary one-sided region boundaries. No dilation, hole filling or
// closure: every restored pixel must itself have source contrast evidence.
#[derive(Default)]
struct SourceFeatures {
    candidates: usize, restored: usize, pixels: usize, skipped: usize,
    anchor: bool, regions: Vec<u32>,
}
impl SourceFeatures {
    fn json(&self, colors: usize, actual: usize) -> String {
        let mut warnings=Vec::new();
        if self.skipped>0 {warnings.push("\"source-ink-component-budget-exceeded\"");}
        if actual>colors {warnings.push("\"palette-budget-exceeded\"");}
        format!("{{\"mode\":\"dark-anchor-local-ink/1\",\"dark_anchor_reserved\":{},\"candidate_components\":{},\"restored_components\":{},\"restored_pixels\":{},\"skipped_components\":{},\"component_budget\":128,\"detector\":{{\"connectivity\":4,\"contrast_luma\":32,\"opposing_radius_px\":[2,4],\"max_luma\":210,\"core_max_channel\":129,\"min_core_pixels\":3,\"area_px\":[8,4096],\"min_span_px\":8,\"border_exclusion_px\":4}},\"geometry\":\"exact-lattice-on-restored-contact-segments\",\"requested_colors\":{},\"actual_colors\":{},\"protected_regions\":{:?},\"representation\":\"source-supported-pixel-regions\",\"warnings\":[{}]}}",
            self.anchor,self.candidates,self.restored,self.pixels,self.skipped,colors,actual,self.regions,warnings.join(","))
    }
}
fn source_ink(img: &ColorImage) -> Vec<Vec<usize>> {
    let (w,h)=(img.width,img.height);let n=w*h;
    let lum:Vec<i16>=img.pixels.chunks_exact(4).map(|p|((77*p[0] as i32+150*p[1] as i32+29*p[2] as i32)/256) as i16).collect();
    let mut mask=vec![false;n];
    if w<9 || h<9 {return Vec::new();}
    for y in 4..h-4 {for x in 4..w-4 {let p=y*w+x;
        if lum[p]>210 || img.pixels[p*4+3]!=255 {continue;}
        'axis: for (dx,dy) in [(1isize,0isize),(0,1),(1,1),(1,-1)] {for r in 2..=4 {
            let d=dy*r*w as isize+dx*r;
            if lum[(p as isize+d) as usize]-lum[p]>=32 && lum[(p as isize-d) as usize]-lum[p]>=32 {mask[p]=true;break 'axis;}
        }}
    }}
    let mut result=Vec::new();
    for i in 0..n {if !mask[i] {continue;}
        let mut stack=vec![i];let mut pixels=Vec::new();mask[i]=false;
        let (mut count,mut core,mut x0,mut x1,mut y0,mut y1)=(0,0,w,0,h,0);
        while let Some(p)=stack.pop() {count+=1;let x=p%w;let y=p/w;
            x0=x0.min(x);x1=x1.max(x);y0=y0.min(y);y1=y1.max(y);
            if img.pixels[p*4..p*4+3].iter().max().unwrap()<&130 {core+=1;}
            if pixels.len()<4096 {pixels.push(p);}
            for q in [p-1,p+1,p-w,p+w] {if mask[q] {mask[q]=false;stack.push(q);}}
        }
        if count>=8 && count<=4096 && (x1-x0+1).max(y1-y0+1)>=8 && core>=3 {result.push(pixels);}
    }
    result
}
fn restore_ink(map: &mut LabelMap, img: &ColorImage, mut candidates: Vec<Vec<usize>>, report: &mut SourceFeatures) {
    // Largest coherent evidence first, deterministic scan-order tie break.
    candidates.sort_by_key(|v|std::cmp::Reverse(v.len()));
    for component in candidates {
        let lost=component.iter().filter(|&&p| {
            let l=map.labels[p];if l==OUTSIDE{return false;}
            let c=map.paints[l as usize].color();let src=&img.pixels[p*4..p*4+3];
            let a=77*c.r as i32+150*c.g as i32+29*c.b as i32;
            let b=77*src[0] as i32+150*src[1] as i32+29*src[2] as i32;
            a-b>=32*256
        }).count();
        if lost==0 {continue;}
        report.candidates+=1;
        if report.restored>=128 {report.skipped+=1;continue;}
        let mut sum=[0u64;3];let mut count=0;
        for &p in &component {let src=&img.pixels[p*4..p*4+3];if *src.iter().max().unwrap()<130 {
            count+=1;for k in 0..3 {sum[k]+=src[k] as u64;}
        }}
        let target=[(sum[0]/count) as i32,(sum[1]/count) as i32,(sum[2]/count) as i32];
        let paint=map.paints.iter().filter(|c|{let c=c.color();c.r.max(c.g).max(c.b)<130})
            .min_by_key(|c|{let c=c.color();[c.r,c.g,c.b].iter().zip(target).map(|(a,b)|(*a as i32-b).pow(2)).sum::<i32>()}).copied();
        let Some(paint)=paint else {report.skipped+=1;continue;};
        let id=map.paints.len() as u32;map.paints.push(paint);report.regions.push(id);
        for p in component {if map.labels[p]!=OUTSIDE {map.labels[p]=id;report.pixels+=1;}}
        report.restored+=1;
    }
}

fn main() -> Result<(),Box<dyn std::error::Error>> {
    let a:Vec<String>=env::args().collect();
    if a.get(1).map(String::as_str)==Some("--map") {return run_map(&a);}
    if a.len()!=8 {return Err("usage: pbn_worker INPUT.rgba WIDTH HEIGHT OUTDIR COLORS SPECKLE SIMPLIFY".into());}
    let colors:usize=a[5].parse()?;let speckle:usize=a[6].parse()?;let simplify:f64=a[7].parse()?;
    if !(2..=64).contains(&colors) || speckle>32 || !simplify.is_finite() || !(0.0..=5.0).contains(&simplify) {return Err("invalid options".into());}
    let w:usize=a[2].parse()?;let h:usize=a[3].parse()?;
    if w==0 || h==0 || w.checked_mul(h).filter(|n| *n<=40_000_000).is_none() {return Err("invalid dimensions/pixel budget".into());}
    let pixels=fs::read(&a[1])?;if pixels.len()!=w*h*4 {return Err("RGBA length mismatch".into());}
    if pixels.chunks_exact(4).any(|p| p[3]!=0 && p[3]!=255) {return Err("spike supports only opaque or binary alpha; explicit policy required".into());}
    let out=Path::new(&a[4]);fs::create_dir_all(out)?;
    let mut cfg=Config::default();cfg.hierarchical=Hierarchical::Cutout;
    cfg.max_colors=Some(colors);cfg.filter_speckle=speckle;cfg.simplify=Some(simplify);cfg.path_precision=Some(3);
    fs::write(out.join("config.txt"),format!("{cfg:#?}"))?;
    let pipeline=cfg.build()?;
    let img=ColorImage{pixels,width:w,height:h};
    let start=std::time::Instant::now();
    let cached=pipeline.segment_with_progress(&img,&CancelToken::new(),&mut |p| eprintln!("progress {p:?}"))?;
    let segment_ms=start.elapsed().as_secs_f64()*1000.0;
    let candidates=source_ink(&img);
    let mut features=SourceFeatures::default();
    let mut seg=cached.clone();
    // Reserve one real source-dark representative before area-weighted palette
    // fitting. Quantize the remaining layers with the remaining budget; do not
    // let their much larger areas swallow a dark accent into blue or brown.
    use vtracer::colorfit::{AutoQuantize, ColorFitter};
    let dark:Vec<bool>=seg.layers.iter().map(|l| {
        let c=l.paint.color(); c.r.max(c.g).max(c.b)<=24 && l.mask.area()>=8
    }).collect();
    let has_dark=dark.iter().any(|v|*v) || !candidates.is_empty();
    features.anchor=has_dark;
    let mut reserved_ink=None;
    if has_dark {
        let mut rest=seg.clone();
        rest.layers=rest.layers.into_iter().zip(&dark).filter_map(|(l,d)|if !d {Some(l)}else{None}).collect();
        AutoQuantize{max_colors:colors-1}.fit(&mut rest);
        let mut fitted=rest.layers.iter();
        let mut sum=[0u64;3];let mut area=0u64;
        for (l,d) in seg.layers.iter().zip(&dark) {if *d {let c=l.paint.color();let n=l.mask.area() as u64;area+=n;
            for (k,v) in [c.r,c.g,c.b].iter().enumerate(){sum[k]+=*v as u64*n;}
        }}
        if area==0 {
            // No near-black layer survived speckle: reserve the source core's
            // measured representative, not an invented black paint.
            for component in &candidates {for &p in component {let c=&img.pixels[p*4..p*4+3];
                if *c.iter().max().unwrap()<130 {area+=1;for k in 0..3 {sum[k]+=c[k] as u64;}}
            }}
        }
        let ink=vtracer::ir::Paint::Solid(vtracer::Color::new((sum[0]/area.max(1)) as u8,(sum[1]/area.max(1)) as u8,(sum[2]/area.max(1)) as u8));
        reserved_ink=Some(ink);
        for (l,d) in seg.layers.iter_mut().zip(&dark) {l.paint=if *d {ink}else{fitted.next().unwrap().paint};}
    } else {for fitter in &pipeline.color_fitters {fitter.fit(&mut seg);}}
    let merge_diff=match &pipeline.compositing {Compositing::Mosaic{merge_diff,..}=>*merge_diff,_=>unreachable!()};
    let merge_diff=if has_dark {0}else{merge_diff};
    let mut map=LabelMap::from_segmentation(&seg);map.merge_similar(merge_diff);
    if let Some(ink)=reserved_ink {if !map.paints.contains(&ink) {map.paints.push(ink);}}
    restore_ink(&mut map,&img,candidates,&mut features);
    // Restoration must not invent same-paint numbered partitions. Coalesce
    // exact equal neighbors only (no weighted-color drift beyond the budget).
    let protected_pixels:Vec<usize>=map.labels.iter().enumerate().filter_map(|(i,l)|if features.regions.contains(l){Some(i)}else{None}).collect();
    map.merge_similar(0);
    features.regions=protected_pixels.iter().map(|&i|map.labels[i]).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
    let actual=map.paints.iter().map(|p|{let c=p.color();(c.r,c.g,c.b)}).collect::<std::collections::BTreeSet<_>>().len();
    let metadata=features.json(colors,actual);
    let mut protected_mask=vec![false;w*h];for i in protected_pixels {protected_mask[i]=true;}
    export_map(&map, &pipeline, out, start, segment_ms, merge_diff, if has_dark {1}else{pipeline.color_fitters.len()}, Some(&cached), Some((&metadata,&protected_mask)))
}

// Exact ownership input: no frontend, color fitting, or merge_similar call.
fn run_map(a: &[String]) -> Result<(),Box<dyn std::error::Error>> {
    if a.len()!=8 {return Err("usage: pbn_worker --map LABELS.u32le WIDTH HEIGHT OUTDIR PALETTE.rgba SIMPLIFY".into());}
    let w:u32=a[3].parse()?;let h:u32=a[4].parse()?;
    let n=(w as usize).checked_mul(h as usize).filter(|n| *n>0 && *n<=40_000_000).ok_or("invalid dimensions/pixel budget")?;
    if w==0 || h==0 {return Err("invalid dimensions".into());}
    let simplify:f64=a[7].parse()?;
    if !simplify.is_finite() || !(0.0..=5.0).contains(&simplify) {return Err("invalid simplify".into());}
    if fs::metadata(&a[2])?.len()!= (n*4) as u64 {return Err("label byte length mismatch".into());}
    let palette_len=fs::metadata(&a[6])?.len();
    if palette_len==0 || palette_len%4!=0 || palette_len>4_000_000 {return Err("invalid palette byte budget".into());}
    let palette=fs::read(&a[6])?;
    if palette.chunks_exact(4).any(|p|p[3]!=255) {return Err("palette must be opaque".into());}
    let bytes=fs::read(&a[2])?;
    if bytes.len()!=n*4 {return Err("label byte length mismatch".into());}
    let labels:Vec<u32>=bytes.chunks_exact(4).map(|b|u32::from_le_bytes(b.try_into().unwrap())).collect();
    if labels.iter().any(|l|*l as usize>=palette.len()/4) {return Err("invalid label identifier".into());}
    let paints=palette.chunks_exact(4).map(|p|vtracer::ir::Paint::Solid(vtracer::Color::new(p[0],p[1],p[2]))).collect();
    let map=LabelMap{width:w,height:h,labels,paints};
    let mut cfg=Config::default();cfg.hierarchical=Hierarchical::Cutout;cfg.simplify=Some(simplify);cfg.path_precision=Some(3);
    let pipeline=cfg.build()?;
    let out=Path::new(&a[5]);
    for name in ["graph.json","labels.u32le","components4.u32le","config.txt","preview.svg","manifest.json"] {
        if out.join(name).symlink_metadata().is_ok() {return Err("refusing existing artifact".into());}
    }
    fs::create_dir_all(out)?;
    fs::write(out.join("config.txt"),format!("exact-map: no segmentation/color fitting/region merging\n{cfg:#?}"))?;
    export_map(&map,&pipeline,out,std::time::Instant::now(),0.0,-1,0,None,None)
}

fn export_map(map: &LabelMap, pipeline: &vtracer::Pipeline, out: &Path,
    start: std::time::Instant, segment_ms: f64, merge_diff: i32, color_fitters: usize,
    cached: Option<&vtracer::Segmentation>, features: Option<(&str,&Vec<bool>)>) -> Result<(),Box<dyn std::error::Error>> {
    let (w,h)=(map.width as usize,map.height as usize);
    let fitter=match &pipeline.compositing {Compositing::Mosaic{fitter,..}=>fitter,_=>unreachable!()};
    let graph=BoundaryGraph::extract(&map);let faces=assemble(&graph,&map);
    let fitted:Vec<_>=graph.segments.iter().map(|s| {
        if features.is_some_and(|(_,mask)|s.points.iter().any(|p| {
            // Only segments touching restored pixel squares use exact lattice
            // geometry; an equal-color union must not pixelate distant edges.
            [(p.x-1,p.y-1),(p.x,p.y-1),(p.x-1,p.y),(p.x,p.y)].iter().any(|&(x,y)|
                x>=0 && y>=0 && (x as usize)<w && (y as usize)<h && mask[y as usize*w+x as usize])
        })) {
            return FittedGeom::Polyline(s.points.iter().map(|p|vtracer::PointF64{x:p.x as f64,y:p.y as f64}).collect());
        }
        let mut g=if s.is_ring(){fitter.fit_ring(s).geom}else{fitter.fit_open(s).geom};
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
    write!(f,"{{\"schema\":\"native-gate.graph/1\",\"width\":{w},\"height\":{h},\"outside\":{OUTSIDE},\"merge_diff\":{merge_diff},\"color_fitters\":{},\"curve_passes\":{},\"segment_ms\":{segment_ms},\"palette\":[",color_fitters,pipeline.curve_passes.len())?;
    for (i,p) in map.paints.iter().enumerate(){if i>0{write!(f,",")?;}let c=p.color();write!(f,"[{},{},{},{}]",c.r,c.g,c.b,c.a)?;}
    write!(f,"],\"source_features\":{},\"components\":[",features.map_or("null",|(json,_)|json))?;
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
    fs::write(out.join("preview.svg"),&svg)?;
    let mut upstream_identical=None;
    if let Some(cached)=cached {
        let upstream=pipeline.writer.write(&pipeline.finish(cached)?);
        fs::write(out.join("upstream.svg"),&upstream)?;
        upstream_identical=Some(svg==upstream);
        // Source preservation intentionally differs from unadapted finish. Keep the
        // actual upstream reference artifact; never relabel an adapted SVG as upstream.
    }
    println!("{{\"type\":\"done\",\"faces8\":{},\"components4\":{},\"edges\":{},\"elapsed_ms\":{},\"preview_upstream_identical\":{}}}",faces.len(),comps.len(),graph.segments.len(),start.elapsed().as_secs_f64()*1000.0,upstream_identical.map_or("null",|v|if v {"true"}else{"false"}));
    Ok(())
}
