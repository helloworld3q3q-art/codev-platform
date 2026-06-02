import {
  get,
} from '@/utils/fetch';

const commonUrl = '';

// 健康检查-存活探针
export async function getCheck(): Promise<API.CommonResult_HealthData_> {
  return await get<API.CommonResult_HealthData_>({
    url: `${commonUrl}/api/v1/health/check`,
  });
}

