```js
import { uploadFile, uploadFiles } from '@/utils/fetch';

// 单文件上传
const handleUpload = async (file: File) => {
  try {
    const response = await uploadFile({
      url: '/api/upload',
      file,
      data: { type: 'document' },
      onProgress: (percent) => {
        console.log(`上传进度: ${percent}%`);
      }
    });
    console.log('上传成功:', response);
  } catch (error) {
    console.error('上传失败:', error);
  }
};

// 多文件上传
const handleMultiUpload = async (files: File[]) => {
  try {
    const response = await uploadFiles({
      url: '/api/upload/multiple',
      files,
      onProgress: (percent) => {
        console.log(`上传进度: ${percent}%`);
      }
    });
    console.log('上传成功:', response);
  } catch (error) {
    console.error('上传失败:', error);
  }
};
```

```js
// 简单下载
downloadFile({
  url: '/api/files/download',
  filename: '报表.xlsx',
});

// 带参数下载
downloadFile({
  url: '/api/files/download',
  params: { id: '123', type: 'report' },
  filename: '报表.xlsx',
});

// 使用服务器返回的文件名
downloadFile({
  url: '/api/files/download',
  params: { id: '123' },
});
```
