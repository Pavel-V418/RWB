using NetTopologySuite;
using NetTopologySuite.Geometries;

namespace App.Domain.Common;

/// <summary>Общие геоконстанты: все координаты хранятся в WGS84 (SRID 4326).</summary>
public static class GeoConstants
{
    public const int Wgs84Srid = 4326;

    /// <summary>Фабрика геометрий с SRID 4326. Использовать вместо <c>new Point(...)</c>.</summary>
    public static GeometryFactory Factory { get; } =
        NtsGeometryServices.Instance.CreateGeometryFactory(srid: Wgs84Srid);

    /// <summary>Точка из широты и долготы. NTS хранит X = долгота, Y = широта.</summary>
    public static Point CreatePoint(double lat, double lon) => Factory.CreatePoint(new Coordinate(lon, lat));
}
