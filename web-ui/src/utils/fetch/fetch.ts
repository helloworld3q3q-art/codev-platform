// 核心
// 提供请求方法，并处理response
// @ts-nocheck
/* eslint-disable */
import { message } from 'antd';
import axios, { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios';
import { BaseApiResponse } from './types';

// 重复请求合并管理器 - 合并1秒内的重复请求
class DuplicateRequestManager {
  private pendingRequests: Map<string, { promise: Promise<any>; timestamp: number }> = new Map();
  private readonly MERGE_WINDOW = 1000; // 1秒内的重复请求将被合并

  // 生成请求唯一标识
  private generateRequestKey(method: string, url: string, data?: any): string {
    const dataStr = data ? JSON.stringify(data) : '';
    return `${method.toUpperCase()}_${url}_${dataStr}`;
  }

  // 获取或创建请求
  public getOrCreateRequest<T>(
    method: string,
    url: string,
    data: any,
    requestFactory: () => Promise<T>,
  ): Promise<T> {
    const key = this.generateRequestKey(method, url, data);
    const now = Date.now();

    // 检查是否存在进行中的相同请求
    const existingRequest = this.pendingRequests.get(key);
    if (existingRequest) {
      // 检查时间窗口，如果在1秒内则合并请求
      if (now - existingRequest.timestamp <= this.MERGE_WINDOW) {
        return existingRequest.promise as Promise<T>;
      } else {
        // 超过时间窗口，移除旧请求
        this.pendingRequests.delete(key);
      }
    }

    // 创建新请求
    const promise = requestFactory();

    // 存储请求信息
    this.pendingRequests.set(key, {
      promise,
      timestamp: now,
    });

    // 请求完成后清理
    promise.finally(() => {
      // 延迟删除，确保短时间内的重复请求能够被合并
      setTimeout(() => {
        const current = this.pendingRequests.get(key);
        if (current && current.timestamp === now) {
          this.pendingRequests.delete(key);
        }
      }, this.MERGE_WINDOW);
    });

    return promise;
  }

  // 清理所有待处理请求
  public clear(): void {
    this.pendingRequests.clear();
  }

  // 获取当前待处理请求数量
  public getPendingCount(): number {
    return this.pendingRequests.size;
  }
}

// 创建全局实例
const duplicateRequestManager = new DuplicateRequestManager();

// 导出管理工具
export const requestUtils = {
  clearAllPendingRequests: () => duplicateRequestManager.clear(),
  getPendingRequestCount: () => duplicateRequestManager.getPendingCount(),
};

// 重新实现 addURLParams 函数
function addURLParams(url: string, params: Record<string, any>): string {
  if (!params || Object.keys(params).length === 0) {
    return url;
  }

  const urlObj = new URL(url, window.location.origin);

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      if (Array.isArray(value)) {
        // 处理数组参数
        value.forEach((item) => {
          urlObj.searchParams.append(key, String(item));
        });
      } else {
        urlObj.searchParams.append(key, String(value));
      }
    }
  });

  return urlObj.pathname + urlObj.search;
}

// 创建 axios 实例
const axiosInstance: AxiosInstance = axios.create({
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截：自动注入登录 token + 当前项目上下文 (多租户 X-Project-Id)
axiosInstance.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token');
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  // 当前项目由 useModel('project') 写入 localStorage；后端多租户接口据此路由。
  const projectId = localStorage.getItem('current_project');
  if (projectId) {
    config.headers = config.headers || {};
    config.headers['X-Project-Id'] = projectId;
  }
  return config;
});

// 用来处理 response status
async function responseStateHandler(response: AxiosResponse) {
  if (response.status === 200) {
    return Promise.resolve(response);
  }
  await message.error({
    content: `响应错误，状态码: ${response.status}`,
    duration: 9,
  });

  return Promise.reject(response);
}

// 用来处理 response body 中的 code（result=0 表示成功）
async function responseCodeHandler<T extends BaseApiResponse>(response: T): Promise<T> {
  const code = response.result;
  // result=0 为成功
  if (code === 0) {
    return Promise.resolve(response);
  } else {
    if (response.errors && response.errors.length > 0) {
      await message.error(response.errors[0].errorMessage, 3);
    } else {
      await message.error(response.message, 3);
    }
  }

  return Promise.reject(response);
}

// 请求处理函数
function responseHandle<T extends BaseApiResponse>(response: AxiosResponse) {
  return Promise.resolve(response)
    .then((res) => responseStateHandler(res))
    .then((res) => res.data)
    .then((res) => responseCodeHandler<T>(res));
}

// 响应拦截器
axiosInstance.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('user');
      const current = `${window.location.pathname}${window.location.search}`;
      if (window.location.pathname !== '/user/login') {
        window.location.href = `/user/login?redirect=${encodeURIComponent(current)}`;
      }
      return Promise.reject(error);
    }
    if (error?.response?.status === 403) {
      message.error('无操作权限', 3);
      return Promise.reject(error);
    }
    // 处理其他错误
    if (error.response) {
      // 服务器返回了错误状态码
      return responseStateHandler(error.response);
    } else if (error.request) {
      // 请求已发送但没有收到响应
      message.error('网络请求超时，请检查您的网络连接');
    } else {
      // 请求配置出错
      message.error('请求配置错误');
    }
    return Promise.reject(error);
  },
);

// 包装请求，添加1秒内重复请求合并功能
function wrapRequest<T>(
  method: string,
  url: string,
  data: any,
  requestFactory: () => Promise<T>,
): Promise<T> {
  // 检查是否为文件上传或下载请求，这类请求不进行合并
  if (method === 'UPLOAD' || method === 'DOWNLOAD') {
    return requestFactory();
  }

  // 使用重复请求管理器
  return duplicateRequestManager.getOrCreateRequest(method, url, data, requestFactory);
}

// post请求
export const post = <T extends BaseApiResponse>({
  url,
  data,
}: {
  url: string;
  data: unknown;
}): Promise<T> => {
  const requestFactory = () => {
    const config = { url, method: 'POST' as const, data };
    return axiosInstance
      .post(url, data, config)
      .then((response) => responseHandle<T>(response))
      .catch((ex) => Promise.reject(ex));
  };

  return wrapRequest('POST', url, data, requestFactory);
};

// get请求
export const get = <T extends BaseApiResponse>({
  url,
  data,
}: {
  url: string;
  data?: { [key: string]: any };
}): Promise<T> => {
  const finalUrl = data !== undefined ? addURLParams(url, data) : url;

  const requestFactory = () => {
    const config = { url: finalUrl, method: 'GET' as const };
    return axiosInstance
      .get(finalUrl, config)
      .then((response) => responseHandle<T>(response))
      .catch((ex) => Promise.reject(ex));
  };

  return wrapRequest('GET', url, data, requestFactory);
};

// put请求
export const put = <T extends BaseApiResponse>({
  url,
  data,
}: {
  url: string;
  data?: unknown;
}): Promise<T> => {
  const requestFactory = () => {
    const config = { url, method: 'PUT' as const, data };
    return axiosInstance
      .put(url, data, config)
      .then((response) => responseHandle<T>(response))
      .catch((ex) => Promise.reject(ex));
  };

  return wrapRequest('PUT', url, data, requestFactory);
};

// delete请求
export const dele = <T extends BaseApiResponse>({
  url,
  data,
}: {
  url: string;
  data?: unknown;
}): Promise<T> => {
  const requestFactory = () => {
    const config = { url, method: 'DELETE' as const, data };
    return axiosInstance
      .delete(url, config)
      .then((response) => responseHandle<T>(response))
      .catch((ex) => Promise.reject(ex));
  };

  return wrapRequest('DELETE', url, data, requestFactory);
};

// 自定义请求，处理Response
export const customRequest = <T extends BaseApiResponse>(
  url: string,
  options: AxiosRequestConfig,
): Promise<T> => {
  const method = (options.method || 'GET').toUpperCase();
  const data = options.data || options.params;

  const requestFactory = () => {
    const config = { url, ...options };
    return axiosInstance(config)
      .then((response) => responseHandle<T>(response))
      .catch((ex) => Promise.reject(ex));
  };

  return wrapRequest(method, url, data, requestFactory);
};

// 文件上传请求
export const uploadFile = <T extends BaseApiResponse>({
  url,
  file,
  responseType,
  data,
  onProgress,
  filename = 'file',
}: {
  url: string;
  file: File | Blob;
  responseType: string;
  data?: Record<string, any>;
  onProgress?: (percent: number) => void;
  filename?: string;
}): Promise<T & { errornum?: number; successnum?: number; totalnum?: number }> => {
  const formData = new FormData();
  let _responseType = {};
  if (responseType === 'blob') {
    _responseType = { responseType: 'blob' };
  }
  formData.append(filename, file);

  if (data) {
    Object.entries(data).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        formData.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
      }
    });
  }

  return axiosInstance
    .post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      ..._responseType,
      onUploadProgress: onProgress
        ? (progressEvent) => {
            const percentCompleted = Math.round(
              (progressEvent.loaded * 100) / (progressEvent.total || 1),
            );
            onProgress(percentCompleted);
          }
        : undefined,
    })
    .then((response) => {
      const headers = response.headers;
      const errornum = headers['errornum'] ? parseInt(headers['errornum'], 10) : undefined;
      const successnum = headers['successnum'] ? parseInt(headers['successnum'], 10) : undefined;
      const totalnum = headers['totalnum'] ? parseInt(headers['totalnum'], 10) : undefined;
      const contentDisposition = headers['content-disposition']
        ? headers['content-disposition']
        : undefined;
      return Promise.resolve(response)
        .then((res) => responseStateHandler(res))
        .then((res) => res.data)
        .then((data) => {
          return {
            data: data,
            errornum,
            successnum,
            totalnum,
            contentDisposition,
          } as unknown as T;
        });
    })
    .catch((ex) => Promise.reject(ex));
};

// 多文件上传请求
export const uploadFiles = <T extends BaseApiResponse>({
  url,
  files,
  data,
  onProgress,
  fileFieldName = 'files',
}: {
  url: string;
  files: File[] | Blob[];
  data?: Record<string, any>;
  onProgress?: (percent: number) => void;
  fileFieldName?: string;
}): Promise<T> => {
  const formData = new FormData();

  files.forEach((file, index) => {
    formData.append(`${fileFieldName}[${index}]`, file);
  });

  if (data) {
    Object.entries(data).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        formData.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
      }
    });
  }

  return axiosInstance
    .post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      onUploadProgress: onProgress
        ? (progressEvent) => {
            const percentCompleted = Math.round(
              (progressEvent.loaded * 100) / (progressEvent.total || 1),
            );
            onProgress(percentCompleted);
          }
        : undefined,
    })
    .then((response) => responseHandle<T>(response))
    .catch((ex) => Promise.reject(ex));
};

// 获取文件Blob对象
export const getFileBlob = async (
  url: string,
  params?: Record<string, any>,
): Promise<{ blob: Blob; headers?: Record<string, string> }> => {
  const finalUrl = params ? addURLParams(url, params) : url;

  try {
    const response = await axiosInstance.get(finalUrl, {
      responseType: 'blob',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    return {
      blob: response.data,
      headers: response.headers as Record<string, string>,
    };
  } catch (error) {
    message.error('文件下载失败');
    throw error;
  }
};

export const downloadFile = async ({
  url,
  params,
  filename,
}: {
  url: string;
  params?: Record<string, any>;
  filename?: string;
}): Promise<void> => {
  try {
    const { blob, headers } = await getFileBlob(url, params);

    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;

    if (filename) {
      link.download = filename;
    } else {
      const contentDisposition = headers?.['content-disposition'];

      if (contentDisposition) {
        const filenameRegex = /filename[^;=\n]*=((['"]).+?\2|[^;\n]*)/;
        const matches = filenameRegex.exec(contentDisposition);
        if (matches !== null && matches[1]) {
          const extractedFilename = matches[1].replace(/["']/g, '');
          try {
            link.download = decodeURIComponent(extractedFilename);
          } catch (e) {
            link.download = extractedFilename;
          }
        }
      }

      if (!link.download) {
        const mimeType = blob.type;
        let extension = '';
        let parts: string[];
        if (mimeType) {
          switch (mimeType) {
            case 'application/pdf':
              extension = '.pdf';
              break;
            case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
              extension = '.xlsx';
              break;
            case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
              extension = '.docx';
              break;
            case 'application/vnd.ms-excel':
              extension = '.xls';
              break;
            case 'application/msword':
              extension = '.doc';
              break;
            case 'text/csv':
              extension = '.csv';
              break;
            case 'image/jpeg':
              extension = '.jpg';
              break;
            case 'image/png':
              extension = '.png';
              break;
            default:
              parts = mimeType.split('/');
              if (parts.length === 2) {
                extension = `.${parts[1].split(';')[0]}`;
              }
          }
        }

        const urlParts = url.split('/');
        let urlFileName = urlParts[urlParts.length - 1].split('?')[0];

        if (urlFileName && !urlFileName.includes('.') && extension) {
          urlFileName += extension;
        }

        link.download = urlFileName || `file${extension}`;
      }
    }

    document.body.appendChild(link);
    link.click();

    setTimeout(() => {
      document.body.removeChild(link);
      window.URL.revokeObjectURL(downloadUrl);
    }, 100);

    return Promise.resolve();
  } catch (error) {
    return Promise.reject(error);
  }
};

// 通过POST方法获取文件Blob对象
export const postFileBlob = async (
  url: string,
  data?: Record<string, any>,
): Promise<{ blob: Blob; headers?: Record<string, string>; status?: number }> => {
  try {
    const response = await axiosInstance.post(url, data, {
      responseType: 'blob',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    return {
      blob: response.data,
      headers: response.headers as Record<string, string>,
      status: response.status,
    };
  } catch (error) {
    message.error('文件导出失败');
    throw error;
  }
};

// 通过POST方法下载文件
export const downloadFileByPost = async ({
  url,
  data,
  filename,
}: {
  url: string;
  data?: Record<string, any>;
  filename?: string;
}): Promise<void> => {
  try {
    const { blob, headers, status } = await postFileBlob(url, data);

    const contentType = headers?.['content-type'] || '';
    const isErrorStatus = status && status >= 400;
    const isJsonResponse = contentType.includes('application/json');

    if (isErrorStatus || isJsonResponse) {
      try {
        const text = await blob.text();
        const errorData = JSON.parse(text);
        const errorMessage =
          errorData?.errors?.[0]?.errorMessage || errorData?.message || '文件导出失败';
        message.error(errorMessage);
        const error = new Error(errorMessage) as any;
        error.errorData = errorData;
        error.status = status;
        throw error;
      } catch (parseError) {
        const errorMessage = '文件导出失败';
        message.error(errorMessage);
        const error = new Error(errorMessage) as any;
        error.status = status;
        throw error;
      }
    }

    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;

    if (filename) {
      link.download = filename;
    } else {
      const contentDisposition = headers?.['content-disposition'];

      if (contentDisposition) {
        const filenameRegex = /filename[^;=\n]*=((['"]).+?\2|[^;\n]*)/;
        const matches = filenameRegex.exec(contentDisposition);

        if (matches !== null && matches[1]) {
          const extractedFilename = matches[1].replace(/['"]*/g, '').trim();
          try {
            link.download = decodeURIComponent(extractedFilename);
          } catch (e) {
            link.download = extractedFilename;
          }
        }
      }

      if (!link.download) {
        const mimeType = blob.type;
        let extension = '';
        let parts: string[];
        if (mimeType) {
          switch (mimeType) {
            case 'application/pdf':
              extension = '.pdf';
              break;
            case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
              extension = '.xlsx';
              break;
            case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
              extension = '.docx';
              break;
            case 'application/vnd.ms-excel':
              extension = '.xls';
              break;
            case 'application/msword':
              extension = '.doc';
              break;
            case 'text/csv':
              extension = '.csv';
              break;
            case 'image/jpeg':
              extension = '.jpg';
              break;
            case 'image/png':
              extension = '.png';
              break;
            default:
              parts = mimeType.split('/');
              if (parts.length === 2) {
                extension = `.${parts[1].split(';')[0]}`;
              }
          }
        }

        link.download = `download_${new Date().getTime()}${extension}`;
      }
    }

    document.body.appendChild(link);
    link.click();

    setTimeout(() => {
      document.body.removeChild(link);
      window.URL.revokeObjectURL(downloadUrl);
    }, 100);

    return Promise.resolve();
  } catch (error) {
    return Promise.reject(error);
  }
};
