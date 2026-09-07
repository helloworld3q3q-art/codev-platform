import { AxiosError } from 'axios';
// 提供基础的request 类型和 response类型

// Response 结构
export interface BaseApiResponse<T = unknown> {
  result?: number; // 0 表示成功
  message?: string;
  data?: T; // 保持可选
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  msgId?: string;
  success?: boolean;
  errors?: Array<{ errorCode?: string; errorMessage?: string; field?: string }>;
}

export interface BasePaginationRequest<T = unknown> {
  current: number;
  pageSize: number;
  params: T;
}

export interface BasePaginationRequestOutsideParams {
  pageSize: number;
  current: number;
}

// 返回 分页 基类
export interface BasePaginationResponse<T = unknown> {
  current: number;
  size: number;
  total: number;
  totalCounts: number;
  pages: number;
  list: T[] | null;
}

export type ResponseError<T = unknown> = AxiosError | BaseApiResponse<T> | Error;

// 兼容旧版 CommonResult / PageResponse（供 stockapi.ts 过渡期使用）
export type CommonResult<T> = BaseApiResponse<T>;

export type PageResponse<T> = BaseApiResponse<T[]> & {
  total: number;
  currentPage: number;
  pageSize: number;
  totalPage: number;
};
