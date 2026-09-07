declare module 'slash2';
declare module '*.css';
// 静态资源声明，保证 Umi/TypeScript 能识别图片、视频和样式导入。
declare module '*.less';
declare module '*.scss';
declare module '*.sass';
declare module '*.svg';
declare module '*.png';
declare module '*.jpg';
declare module '*.jpeg';
declare module '*.gif';
declare module '*.bmp';
declare module '*.tiff';
declare module 'omit.js';
declare module 'numeral';
declare module 'mockjs';
declare module 'react-fittext';
// three 无随包类型声明（未装 @types/three）；codegraph 3D 图谱仅用少量 API，按 any 放行。
declare module 'three';

declare const REACT_APP_ENV: 'test' | 'dev' | 'pre' | false;
