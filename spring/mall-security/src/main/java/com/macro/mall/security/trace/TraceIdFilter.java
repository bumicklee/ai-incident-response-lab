package com.macro.mall.security.trace;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * [다온마켓 추가] 모든 요청의 맨 앞에서 추적 번호를 정한다(TraceIdConfig가 가장 먼저 돌게 등록한다).
 * Spring Security·다온 운영 도구 필터보다 앞이라, 거기서 남기는 SECURITY 이벤트도 같은 번호를 쓴다.
 */
public class TraceIdFilter extends OncePerRequestFilter {
    /** secure-kit TraceIdFilter가 정한 번호를 넣는 요청 속성(io.github.securekit.web.KitContext.TRACE_ATTR) */
    static final String KIT_TRACE_ATTR = "secure-kit.trace-id";

    private static HttpServletRequest withTraceHeader(HttpServletRequest request, String id) {
        if (id.equals(request.getHeader(TraceId.HEADER))) {
            return request;
        }
        return new HttpServletRequestWrapper(request) {
            @Override
            public String getHeader(String name) {
                return TraceId.HEADER.equalsIgnoreCase(name) ? id : super.getHeader(name);
            }

            @Override
            public java.util.Enumeration<String> getHeaders(String name) {
                return TraceId.HEADER.equalsIgnoreCase(name)
                        ? java.util.Collections.enumeration(java.util.List.of(id)) : super.getHeaders(name);
            }

            @Override
            public java.util.Enumeration<String> getHeaderNames() {
                java.util.Set<String> names = new java.util.LinkedHashSet<>(java.util.Collections.list(super.getHeaderNames()));
                names.add(TraceId.HEADER);
                return java.util.Collections.enumeration(names);
            }
        };
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        // secure-kit: 킷의 추적 필터도 "가장 먼저" 돈다. 킷이 먼저 돌았으면 그 번호를 그대로 쓴다
        String id = request.getAttribute(KIT_TRACE_ATTR) instanceof String kit ? TraceId.accept(kit) : null;
        if (id == null) {
            id = TraceId.accept(request.getHeader(TraceId.HEADER));
        }
        if (id == null) {
            id = TraceId.fromTraceparent(request.getHeader("traceparent"));
        }
        if (id == null) {
            id = TraceId.accept(request.getHeader("X-Request-Id"));
        }
        if (id == null) {
            id = TraceId.newId();
        }
        request.setAttribute(TraceId.ATTR, id);
        response.setHeader(TraceId.HEADER, id);      // 오류 응답에도 붙도록 처리 전에 단다
        MDC.put(TraceId.MDC_KEY, id);
        try {
            // 킷이 뒤에 돌면 X-Trace-Id 머리를 보고 같은 번호를 고르게 한다(머리가 없던 요청도 앱 로그·킷 이벤트가 같은 번호)
            chain.doFilter(withTraceHeader(request, id), response);
        } finally {
            MDC.remove(TraceId.MDC_KEY);             // 스레드를 다음 요청이 다시 쓴다
        }
    }
}
